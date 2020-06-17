from base64 import b64encode
from xml.sax.saxutils import escape
import contextlib
import html
import functools
import io
import logging
import os
import pathlib
import zipfile

from cumulusci.core.sfdx import sfdx
from cumulusci.utils import cd
from cumulusci.utils import inject_namespace
from cumulusci.utils import process_text_in_zipfile
from cumulusci.utils import strip_namespace
from cumulusci.utils import temporary_dir
from cumulusci.utils import tokenize_namespace
from cumulusci.utils import zip_clean_metaxml
from cumulusci.utils.xml import metadata_tree
from cumulusci.utils.ziputils import hash_zipfile_contents

INSTALLED_PACKAGE_PACKAGE_XML = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
  <types>
    <members>{namespace}</members>
    <name>InstalledPackage</name>
  </types>
<version>{version}</version>
</Package>"""

EMPTY_PACKAGE_XML = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
<version>{version}</version>
</Package>"""

FULL_NAME_PACKAGE_XML = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
<fullName>{}</fullName>
<version>{}</version>
</Package>"""

INSTALLED_PACKAGE = """<?xml version="1.0" encoding="UTF-8"?>
<InstalledPackage xmlns="http://soap.sforce.com/2006/04/metadata">
  <versionNumber>{}</versionNumber>
  <activateRSS>{}</activateRSS>
  <securityType>{}</securityType>
  {}
</InstalledPackage>"""

DEFAULT_LOGGER = logging.getLogger(__name__)


class BasePackageZipBuilder(object):
    def __init__(self):
        self._open_zip()

    def _open_zip(self):
        """Start a new, empty zipfile"""
        self.buffer = io.BytesIO()
        self.zf = zipfile.ZipFile(self.buffer, "w", zipfile.ZIP_DEFLATED)

    def _write_package_xml(self, package_xml):
        self.zf.writestr("package.xml", package_xml)

    def _write_file(self, path, content):
        self.zf.writestr(path, content)

    def as_bytes(self):
        fp = self.zf.fp
        self.zf.close()
        value = fp.getvalue()
        fp.close()
        return value

    def as_base64(self):
        return b64encode(self.as_bytes()).decode("utf-8")

    def as_hash(self):
        return hash_zipfile_contents(self.zf)

    def __call__(self):
        # for backwards compatibility
        return self.as_base64()


class MetadataPackageZipBuilder(BasePackageZipBuilder):
    """Build a package zip from a metadata folder."""

    def __init__(
        self,
        *,
        path=None,
        zf: zipfile.ZipFile = None,
        options=None,
        logger=None,
        name=None,
    ):
        self.options = options or {}
        self.logger = logger or DEFAULT_LOGGER
        if zf is not None:
            self.zf = zf
        elif path is not None:
            path = str(path)
            self._open_zip()
            with self._convert_sfdx_format(path, name) as path:
                self._add_files_to_package(path)
        else:
            self._open_zip()
        self._process()

    @classmethod
    def from_zipfile(cls, zf, *, options=None, logger=None):
        """Start with an existing zipfile rather than a filesystem folder."""
        return cls(zf=zf, options=options, logger=logger)

    @contextlib.contextmanager
    def _convert_sfdx_format(self, path, name):
        orig_path = path
        with contextlib.ExitStack() as stack:
            if not pathlib.Path(path, "package.xml").exists():
                self.logger.info("Converting from sfdx to mdapi format")
                path = stack.enter_context(temporary_dir(chdir=False))
                args = ["-r", orig_path, "-d", path]
                if name:
                    args += ["-n", name]
                sfdx(
                    "force:source:convert",
                    args=args,
                    capture_output=False,
                    check_return=True,
                )

            yield path

    def _add_files_to_package(self, path):
        for file_path in self._find_files_to_package(path):
            self.zf.write(file_path, arcname=str(file_path.relative_to(path)))

    def _find_files_to_package(self, path):
        """Generator of paths to include in the package.

        Walks through all directories and files in path,
        filtering using _include_directory and _include_file
        """
        for root, dirs, files in os.walk(path):
            root_parts = root[len(path) :].split(os.sep)[1:]
            if self._include_directory(root_parts):
                for f in files:
                    if self._include_file(root_parts, f):
                        yield pathlib.Path(root, f)

    def _include_directory(self, root_parts):
        """Return boolean for whether this directory should be included in the package."""
        # include the root directory, all non-lwc directories and sub-directories, and lwc component directories
        return len(root_parts) == 0 or root_parts[0] != "lwc" or len(root_parts) == 2

    def _include_file(self, root_parts, f):
        """Return boolean for whether this file should be included in the package."""
        if len(root_parts) == 2 and root_parts[0] == "lwc":
            # is file of lwc component directory
            lower_f = f.lower()
            return lower_f.endswith((".js", ".js-meta.xml", ".html", ".css", ".svg"))
        return True

    def _process(self):
        self._process_namespace_tokens()
        self._clean_meta_xml()
        self._bundle_staticresources()

    def _process_namespace_tokens(self):
        zipf = self.zf
        if self.options.get("namespace_tokenize"):
            self.logger.info(
                f"Tokenizing namespace prefix {self.options['namespace_tokenize']}__"
            )
            zipf = process_text_in_zipfile(
                zipf,
                functools.partial(
                    tokenize_namespace,
                    namespace=self.options["namespace_tokenize"],
                    logger=self.logger,
                ),
            )
        if self.options.get("namespace_inject"):
            managed = not self.options.get("unmanaged", True)
            if managed:
                self.logger.info(
                    "Replacing namespace tokens from metadata with namespace prefix  "
                    f"{self.options['namespace_inject']}__"
                )
            else:
                self.logger.info(
                    "Stripping namespace tokens from metadata for unmanaged deployment"
                )
            zipf = process_text_in_zipfile(
                zipf,
                functools.partial(
                    inject_namespace,
                    namespace=self.options["namespace_inject"],
                    managed=managed,
                    namespaced_org=self.options.get("namespaced_org", False),
                    logger=self.logger,
                ),
            )
        if self.options.get("namespace_strip"):
            self.logger.info("Stripping namespace tokens from metadata")
            zipf = process_text_in_zipfile(
                zipf,
                functools.partial(
                    strip_namespace,
                    namespace=self.options["namespace_strip"],
                    logger=self.logger,
                ),
            )
        self.zf = zipf

    def _clean_meta_xml(self):
        if not self.options.get("clean_meta_xml", True):
            return
        self.logger.info(
            "Cleaning meta.xml files of packageVersion elements for deploy"
        )
        zf = zip_clean_metaxml(self.zf)
        self.zf.close()
        self.zf = zf

    def _bundle_staticresources(self):
        relpath = self.options.get("static_resource_path")
        if not relpath or not os.path.exists(relpath):
            return
        path = os.path.realpath(relpath)

        # Copy existing files to new zipfile
        zip_dest = zipfile.ZipFile(io.BytesIO(), "w", zipfile.ZIP_DEFLATED)
        for name in self.zf.namelist():
            if name == "package.xml":
                package_xml = self.zf.open(name)
            else:
                content = self.zf.read(name)
                zip_dest.writestr(name, content)

        # Build static resource bundles and add to package
        with temporary_dir():
            os.mkdir("staticresources")
            bundles = []
            for name in os.listdir(path):
                bundle_relpath = os.path.join(relpath, name)
                bundle_path = os.path.join(path, name)
                if not os.path.isdir(bundle_path):
                    continue
                self.logger.info(
                    "Zipping {} to add to staticresources".format(bundle_relpath)
                )

                # Add resource-meta.xml file
                meta_name = "{}.resource-meta.xml".format(name)
                meta_path = os.path.join(path, meta_name)
                with open(meta_path, "rb") as f:
                    zip_dest.writestr("staticresources/{}".format(meta_name), f.read())

                # Add bundle
                zip_path = os.path.join("staticresources", "{}.resource".format(name))
                with open(zip_path, "wb") as bundle_fp:
                    bundle_zip = zipfile.ZipFile(bundle_fp, "w", zipfile.ZIP_DEFLATED)
                    with cd(bundle_path):
                        for root, dirs, files in os.walk("."):
                            for f in files:
                                resource_file = os.path.join(root, f)
                                bundle_zip.write(resource_file)
                    bundle_zip.close()
                zip_dest.write(zip_path)
                bundles.append(name)

        # Update package.xml
        Package = metadata_tree.parse(package_xml)
        sections = Package.findall("types", name="StaticResource")
        section = sections[0] if sections else None
        if not section:
            section = Package.append("types")
            section.append("name", text="StaticResource")
        for name in bundles:
            section.insert_before(section.find("name"), tag="members", text=name)
        package_xml = Package.tostring(xml_declaration=True)
        zip_dest.writestr("package.xml", package_xml)

        self.zf.close()
        self.zf = zip_dest


class CreatePackageZipBuilder(BasePackageZipBuilder):
    def __init__(self, name, api_version):
        if not name:
            raise ValueError("You must provide a name to create a package")
        if not api_version:
            raise ValueError("You must provide an api_version to create a package")
        self.name = name
        self.api_version = api_version

        self._open_zip()
        self._populate_zip()

    def _populate_zip(self):
        package_xml = FULL_NAME_PACKAGE_XML.format(escape(self.name), self.api_version)
        self._write_package_xml(package_xml)


class InstallPackageZipBuilder(BasePackageZipBuilder):
    api_version = "43.0"

    def __init__(
        self, namespace, version, activateRSS=False, password=None, securityType="FULL"
    ):
        if not namespace:
            raise ValueError("You must provide a namespace to install a package")
        if not version:
            raise ValueError("You must provide a version to install a package")
        self.namespace = namespace
        self.version = version
        self.activateRSS = activateRSS
        self.password = password
        self.securityType = securityType

        self._open_zip()
        self._populate_zip()

    def _populate_zip(self):
        package_xml = INSTALLED_PACKAGE_PACKAGE_XML.format(
            namespace=self.namespace, version=self.api_version
        )
        self._write_package_xml(package_xml)

        activateRSS = "true" if self.activateRSS else "false"
        password = (
            "<password>{}</password>".format(html.escape(self.password))
            if self.password
            else ""
        )
        installed_package = INSTALLED_PACKAGE.format(
            self.version, activateRSS, self.securityType, password
        )
        self._write_file(
            "installedPackages/{}.installedPackage".format(self.namespace),
            installed_package,
        )


class DestructiveChangesZipBuilder(BasePackageZipBuilder):
    def __init__(self, destructive_changes, version):
        self.destructive_changes = destructive_changes
        self.version = version

        self._open_zip()
        self._populate_zip()

    def _populate_zip(self):
        self._write_package_xml(EMPTY_PACKAGE_XML.format(version=self.version))
        self._write_file("destructiveChanges.xml", self.destructive_changes)


class UninstallPackageZipBuilder(DestructiveChangesZipBuilder):
    def __init__(self, namespace, version):
        if not namespace:
            raise ValueError("You must provide a namespace to install a package")
        self.namespace = namespace
        self.version = version
        self.destructive_changes = INSTALLED_PACKAGE_PACKAGE_XML.format(
            namespace=self.namespace, version=self.version
        )

        self._open_zip()
        self._populate_zip()
