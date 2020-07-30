from pathlib import Path
from unittest import mock
import tempfile

from lxml import etree
import pytest

from cumulusci.core.exceptions import CumulusCIException
from cumulusci.tasks.salesforce.tests.util import create_task
from cumulusci.tasks.metadata_etl import (
    BaseMetadataETLTask,
    BaseMetadataSynthesisTask,
    BaseMetadataTransformTask,
    MetadataSingleEntityTransformTask,
    UpdateMetadataFirstChildTextTask,
)
from cumulusci.utils.xml.metadata_tree import MetadataElement


class MetadataETLTask(BaseMetadataETLTask):
    _get_package_xml_content = mock.Mock()
    _transform = mock.Mock()


class TestBaseMetadataETLTask:
    def test_init_options(self):
        task = create_task(MetadataETLTask, {"managed": True, "api_version": "47.0"})

        assert task.options["managed"]
        assert task.options["api_version"] == "47.0"

    def test_inject_namespace(self):
        task = create_task(
            MetadataETLTask,
            {"managed": True, "namespace_inject": "test", "api_version": "47.0"},
        )

        assert task._inject_namespace("%%%NAMESPACE%%%Test__c") == "test__Test__c"
        task.options["managed"] = False
        assert task._inject_namespace("%%%NAMESPACE%%%Test__c") == "Test__c"

    @mock.patch("cumulusci.tasks.metadata_etl.base.ApiRetrieveUnpackaged")
    def test_retrieve(self, api_mock):
        task = create_task(
            MetadataETLTask,
            {"managed": False, "namespace_inject": "test", "api_version": "47.0"},
        )
        task.retrieve_dir = mock.Mock()
        task._get_package_xml_content = mock.Mock()
        task._get_package_xml_content.return_value = ""

        task._retrieve()
        api_mock.assert_called_once_with(
            task, task._generate_package_xml(False), "47.0"
        )
        api_mock.return_value.assert_called_once_with()
        api_mock.return_value.return_value.extractall.assert_called_once_with(
            task.retrieve_dir
        )

    @mock.patch("cumulusci.tasks.salesforce.Deploy")
    def test_deploy(self, deploy_mock):
        with tempfile.TemporaryDirectory() as tmpdir:
            task = create_task(
                MetadataETLTask,
                {"managed": False, "namespace_inject": "test", "api_version": "47.0"},
            )
            task.deploy_dir = Path(tmpdir)
            task._generate_package_xml = mock.Mock()
            task._generate_package_xml.return_value = "test"
            result = task._deploy()
            assert (Path(tmpdir) / "package.xml").read_text() == "test"

            assert len(deploy_mock.call_args_list) == 1

            assert deploy_mock.call_args_list[0][0][0] == task.project_config
            assert deploy_mock.call_args_list[0][0][2] == task.org_config
            deploy_mock.return_value.assert_called_once_with()
            assert result == deploy_mock.return_value.return_value

    def test_run_task(self):
        task = create_task(
            MetadataETLTask,
            {"managed": False, "namespace_inject": "test", "api_version": "47.0"},
        )

        task._retrieve = mock.Mock()
        task._deploy = mock.Mock()
        task.retrieve = True
        task.deploy = True

        task()

        task._retrieve.assert_called_once_with()
        task._deploy.assert_called_once_with()


class MetadataSynthesisTask(BaseMetadataSynthesisTask):
    _get_package_xml_content = mock.Mock()
    _synthesize = mock.Mock()


class TestBaseMetadataSynthesisTask:
    def test_synthesis(self):
        task = create_task(
            MetadataSynthesisTask,
            {"managed": False, "namespace_inject": "test", "api_version": "47.0"},
        )

        task._deploy = mock.Mock()
        task._synthesize()
        task._synthesize = mock.Mock()

        task()

        task._deploy.assert_called_once_with()
        task._synthesize.assert_called_once_with()

    @mock.patch("cumulusci.tasks.metadata_etl.base.PackageXmlGenerator")
    def test_generate_package_xml(self, package_mock):
        task = create_task(
            MetadataSynthesisTask,
            {"managed": False, "namespace_inject": "test", "api_version": "47.0"},
        )
        task.deploy_dir = "test"

        result = task._generate_package_xml(True)
        package_mock.assert_called_once_with(str(task.deploy_dir), task.api_version)
        package_mock.return_value.assert_called_once_with()
        assert result == package_mock.return_value.return_value


class MetadataTransformTask(BaseMetadataTransformTask):
    _get_entities = mock.Mock()
    _transform = mock.Mock()


class TestBaseMetadataTransformTask:
    def test_generate_package_xml(self):
        task = create_task(
            MetadataTransformTask,
            {"managed": False, "namespace_inject": "test", "api_version": "47.0"},
        )

        task._get_entities = mock.Mock()
        task._get_entities.return_value = {
            "CustomObject": {"Account", "Contact"},
            "ApexClass": {"Test"},
        }

        assert (
            task._generate_package_xml(False)
            == """<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
    <types>
        <members>Account</members>
        <members>Contact</members>
        <name>CustomObject</name>
    </types>
    <types>
        <members>Test</members>
        <name>ApexClass</name>
    </types>

    <version>47.0</version>
</Package>
"""
        )


class ConcreteMetadataSingleEntityTransformTask(MetadataSingleEntityTransformTask):
    def _transform_entity(self, xml_tree, api_name):
        return xml_tree


class TestMetadataSingleEntityTransformTask:
    def test_init_options(self):
        task = create_task(ConcreteMetadataSingleEntityTransformTask, {})
        task._init_options(
            {
                "managed": True,
                "api_version": "47.0",
                "namespace_inject": "test",
                "api_names": "%%%NAMESPACE%%%bar,foo",
            }
        )
        assert task.api_names == set(["test__bar", "foo"])

    def test_get_entities(self):
        task = create_task(
            ConcreteMetadataSingleEntityTransformTask,
            {"managed": False, "api_version": "47.0", "api_names": "bar,foo"},
        )

        assert task._get_entities() == {None: {"bar", "foo"}}

        task = create_task(
            ConcreteMetadataSingleEntityTransformTask,
            {"managed": False, "api_version": "47.0"},
        )

        assert task._get_entities() == {None: {"*"}}

    def test_transform(self):
        task = create_task(
            ConcreteMetadataSingleEntityTransformTask,
            {"managed": False, "api_version": "47.0", "api_names": "Test"},
        )

        task.entity = "CustomApplication"
        fake_tree = mock.Mock()
        fake_tree.tostring = mock.Mock(return_value="<pretend-xml/>")
        task._transform_entity = mock.Mock(return_value=fake_tree)

        input_xml = """<?xml version="1.0" encoding="UTF-8"?>
<CustomApplication xmlns="http://soap.sforce.com/2006/04/metadata">
</CustomApplication>"""

        with tempfile.TemporaryDirectory() as tmpdir:
            task._create_directories(tmpdir)

            test_path = task.retrieve_dir / "applications"
            test_path.mkdir()
            test_path = test_path / "Test.app"

            test_path.write_text(input_xml)

            task._transform()

            assert len(task._transform_entity.call_args_list) == 1

    def test_transform__bad_entity(self):
        task = create_task(
            ConcreteMetadataSingleEntityTransformTask,
            {"managed": False, "api_version": "47.0", "api_names": "bar,foo"},
        )

        task.entity = "Battlestar"

        with pytest.raises(CumulusCIException):
            task._transform()

    def test_transform__wildcard(self):
        task = create_task(
            ConcreteMetadataSingleEntityTransformTask,
            {"managed": False, "api_version": "47.0"},
        )

        task.entity = "CustomApplication"
        assert task.api_names == set("*")

        input_xml = """<?xml version="1.0" encoding="UTF-8"?>
<CustomApplication xmlns="http://soap.sforce.com/2006/04/metadata">
</CustomApplication>"""

        with tempfile.TemporaryDirectory() as tmpdir:
            task._create_directories(tmpdir)

            app_path = task.retrieve_dir / "applications"
            app_path.mkdir()
            test_path = app_path / "Test.app"
            test_path.write_text(input_xml)

            test_path = app_path / "Test_2.app"
            test_path.write_text(input_xml)

            task._transform()

            assert task.api_names == set(["Test", "Test_2"])
            assert (task.deploy_dir / "applications" / "Test.app").exists()
            assert (task.deploy_dir / "applications" / "Test_2.app").exists()

    def test_transform__remove_entity(self):
        task = create_task(
            ConcreteMetadataSingleEntityTransformTask,
            {"managed": False, "api_version": "47.0", "api_names": "*"},
        )

        task.entity = "CustomApplication"
        task._transform_entity = mock.Mock(
            side_effect=lambda xml, api_name: None if api_name == "Test_2" else xml
        )

        input_xml = """<?xml version="1.0" encoding="UTF-8"?>
<CustomApplication xmlns="http://soap.sforce.com/2006/04/metadata">
</CustomApplication>"""

        with tempfile.TemporaryDirectory() as tmpdir:
            task._create_directories(tmpdir)

            app_path = task.retrieve_dir / "applications"
            app_path.mkdir()
            test_path = app_path / "Test.app"
            test_path.write_text(input_xml)

            test_path = app_path / "Test_2.app"
            test_path.write_text(input_xml)

            task._transform()

            assert task.api_names == set(["Test"])
            assert (task.deploy_dir / "applications" / "Test.app").exists()
            assert not (task.deploy_dir / "applications" / "Test_2.app").exists()

    def test_transform__encoded_page_layout(self):
        task = create_task(
            ConcreteMetadataSingleEntityTransformTask,
            {"managed": False, "api_version": "47.0", "api_names": "*"},
        )

        task.entity = "Layout"
        task._transform_entity = mock.Mock(side_effect=lambda xml, api_name: xml)

        input_xml = """<?xml version="1.0" encoding="UTF-8"?>
<PageLayout xmlns="http://soap.sforce.com/2006/04/metadata">
</PageLayout>"""

        with tempfile.TemporaryDirectory() as tmpdir:
            task._create_directories(tmpdir)

            app_path = task.retrieve_dir / "layouts"
            app_path.mkdir()
            test_path = app_path / "Contact %28Marketing%29 Layout.layout"
            test_path.write_text(input_xml)

            task._transform()
            assert len(task._transform_entity.call_args_list) == 1
            assert (
                task._transform_entity.call_args_list[0][0][1]
                == "Contact (Marketing) Layout"
            )

            assert task.api_names == set(["Contact %28Marketing%29 Layout"])
            assert (
                task.deploy_dir / "layouts" / "Contact %28Marketing%29 Layout.layout"
            ).exists()

    def test_transform__non_xml_entity(self):
        task = create_task(
            ConcreteMetadataSingleEntityTransformTask,
            {"managed": False, "api_version": "47.0", "api_names": "bar,foo"},
        )

        task.entity = "LightningComponentBundle"

        with pytest.raises(CumulusCIException):
            task._transform()

    def test_transform__missing_record(self):
        task = create_task(
            ConcreteMetadataSingleEntityTransformTask,
            {"managed": False, "api_version": "47.0", "api_names": "Test"},
        )

        task.entity = "CustomApplication"

        with tempfile.TemporaryDirectory() as tmpdir:
            task._create_directories(tmpdir)

            test_path = task.retrieve_dir / "applications"
            test_path.mkdir()

            with pytest.raises(CumulusCIException):
                task._transform()

    def test_transform__xml_parse_error(self):
        task = create_task(
            ConcreteMetadataSingleEntityTransformTask,
            {"managed": False, "api_version": "47.0", "api_names": "Test"},
        )

        task.entity = "CustomApplication"

        with tempfile.TemporaryDirectory() as tmpdir:
            task._create_directories(tmpdir)

            test_path = task.retrieve_dir / "applications"
            test_path.mkdir()
            test_path = test_path / "Test.app"

            test_path.write_text(">>>>>NOT XML<<<<<")
            with pytest.raises(etree.ParseError):
                task._transform()


class TestUpdateMetadataFirstChildTextTask:
    @pytest.mark.parametrize(
        "options,expected_value",
        [
            # 000
            (
                {
                    "managed": False,
                    "namespace_inject": None,
                    "entity": "CustomObject",
                    "tag": "customObjectAttribute",
                    "value": "newAttributeValue",
                },
                "newAttributeValue",
            ),
            # 001
            (
                {
                    "managed": False,
                    "namespace_inject": None,
                    "entity": "CustomObject",
                    "tag": "customObjectAttribute",
                    "value": "newAttributeValue",
                },
                "newAttributeValue",
            ),
            # 010
            (
                {
                    "managed": False,
                    "namespace_inject": "namespace",
                    "entity": "CustomObject",
                    "tag": "customObjectAttribute",
                    "value": "newAttributeValue",
                },
                "newAttributeValue",
            ),
            # 011
            (
                {
                    "managed": False,
                    "namespace_inject": "namespace",
                    "entity": "CustomObject",
                    "tag": "customObjectAttribute",
                    "value": "newAttributeValue",
                },
                "newAttributeValue",
            ),
            # 100
            (
                {
                    "managed": True,
                    "namespace_inject": None,
                    "entity": "CustomObject",
                    "tag": "customObjectAttribute",
                    "value": "newAttributeValue",
                },
                "newAttributeValue",
            ),
            # 101
            (
                {
                    "managed": True,
                    "namespace_inject": None,
                    "entity": "CustomObject",
                    "tag": "customObjectAttribute",
                    "value": "newAttributeValue",
                },
                "newAttributeValue",
            ),
            # 110
            (
                {
                    "managed": True,
                    "namespace_inject": "namespace",
                    "entity": "CustomObject",
                    "tag": "customObjectAttribute",
                    "value": "newAttributeValue",
                },
                "newAttributeValue",
            ),
        ],
        ids=[
            "000: not managed, namespace_inject is None, and value has no namespace token",
            "001: not managed and namespace_inject is None though value has a namespace token",
            "010: not managed and value has no namespace token though namespace_inject is not None",
            "011: not managed though namespace_inject is not None and value has a namespace token",
            "100: namespace_inject is None and value has no namespace token though managed",
            "101: namespace_inject is None though managed and value has a namespace token",
            "110: value has no namespace token though managed and namespace_inject is not None",
        ],
    )
    def test_init_options__namespace_not_injected_in_value(
        self, options, expected_value
    ):
        """
        Namespace is injected into value option if all three are true:
        0) "T": value has a namespace token
        1) "N": namespace_inject is not None
        2) "M": managed is truthy

        This tests all combinations where the namespace is not injected
        into the value option.

        Binary indices are shown below mapping:
        210
        |||
        MNT
        """
        task = create_task(UpdateMetadataFirstChildTextTask, options)

        assert expected_value == task.options["value"]

        # task.entity should always be set as options["entity"]
        assert options["entity"] == task.entity

    @pytest.mark.parametrize(
        "options,expected_value",
        [
            (
                {
                    "managed": True,
                    "namespace_inject": "namespace",
                    "entity": "CustomObject",
                    "tag": "customObjectAttribute",
                    "value": "%%%NAMESPACE%%%newAttributeValue",
                },
                "namespace__newAttributeValue",
            )
        ],
        ids=[
            "value should be namespace injected: managed, namespace_inject is not None, and value has a namespace token"
        ],
    )
    def test_init_options__namespace_injected_in_value(self, options, expected_value):
        task = create_task(UpdateMetadataFirstChildTextTask, options)

        assert expected_value == task.options["value"]

        # task.entity should always be set as options["entity"]
        assert options["entity"] == task.entity

    def test_transform_entity__attribute_found(self):
        api_name = "Supercalifragilisticexpialidocious__c"

        metadata = mock.Mock(spec=MetadataElement)
        metadata.find.return_value = mock.Mock(spec=MetadataElement)
        metadata.append.return_value = mock.Mock(spec=MetadataElement)

        entity = "CustomObject"
        tag = "customObjectAttribute"
        value = "newAttributeValue"

        task = create_task(
            UpdateMetadataFirstChildTextTask,
            {
                "managed": False,
                "namespace_inject": None,
                "entity": entity,
                "tag": tag,
                "value": value,
            },
        )
        task.logger = mock.Mock()
        assert tag == task.options.get("tag")
        assert value == task.options.get("value")

        actual = task._transform_entity(metadata, api_name)

        assert metadata == actual

        metadata.find.assert_called_once_with(tag)
        metadata.append.assert_not_called()
        assert metadata.find.return_value.text == value

        task.logger.info.assert_has_calls(
            [
                mock.call(f'Updating {entity} "{api_name}":'),
                mock.call(f'    {tag} as "{value}"'),
            ]
        )

    def test_transform_entity__attribute_not_found(self):
        api_name = "Supercalifragilisticexpialidocious__c"

        metadata = mock.Mock(spec=MetadataElement)
        metadata.find.return_value = None
        metadata.append.return_value = mock.Mock(spec=MetadataElement)

        entity = "CustomObject"
        tag = "customObjectAttribute"
        value = "newAttributeValue"

        task = create_task(
            UpdateMetadataFirstChildTextTask,
            {
                "managed": False,
                "namespace_inject": None,
                "entity": entity,
                "tag": tag,
                "value": value,
            },
        )
        task.logger = mock.Mock()
        assert tag == task.options.get("tag")
        assert value == task.options.get("value")

        actual = task._transform_entity(metadata, api_name)

        assert metadata == actual

        metadata.find.assert_called_once_with(tag)
        metadata.append.assert_called_once_with(tag)
        assert metadata.append.return_value.text == value

        task.logger.info.assert_has_calls(
            [
                mock.call(f'Updating {entity} "{api_name}":'),
                mock.call(f'    {tag} as "{value}"'),
            ]
        )
