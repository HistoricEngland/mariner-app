from unittest.mock import patch
from uuid import uuid4

from arches.app.models.models import GraphModel, Node, NodeGroup
from arches_querysets.models import TileTree
from django.core.exceptions import ImproperlyConfigured
from django.db import DataError, connection, transaction

from mariner_app.heritage_asset_names import NAME_NODES, REFERENCE_NODES
from mariner_app.heritage_asset_names_aqs import get_heritage_asset_names
from tests import test_heritage_asset_names


class PublicAQSAssetNamesTests(test_heritage_asset_names.HeritageAssetNamesTests):
    """Run the same SQL oracle and edge cases through the public AQS API."""

    query_names = staticmethod(get_heritage_asset_names)
    report_stem = "heritage_asset_names_public_aqs"
    missing_node_message = "AQS heritage asset configuration:"

    def assert_equivalent(self, resource_ids, expected, *, language_code="en"):
        rows = super().assert_equivalent(
            resource_ids, expected, language_code=language_code
        )
        original_rows = list(
            test_heritage_asset_names.get_heritage_asset_names(
                resource_ids, language_code=language_code
            )
        )
        self.assertCountEqual(rows, original_rows)
        return rows

    def test_node_alias_requirements(self):
        # Unlike the UUID-based version, this API deliberately requires aliases.
        for alias in (None, "name"):
            with self.subTest(alias=alias):
                Node.objects.filter(pk=NAME_NODES[0][1]).update(alias=alias)
                with self.assertRaisesMessage(
                    ImproperlyConfigured, "monument.monument_name"
                ):
                    self.query_names([uuid4()])

    def test_missing_graph_slug_raises_configuration_error(self):
        GraphModel.objects.filter(slug="monument").update(slug=None)
        with self.assertRaisesMessage(ImproperlyConfigured, "monument.monument_names"):
            self.query_names([uuid4()])

    def test_misplaced_value_node_raises_configuration_error(self):
        Node.objects.filter(pk=NAME_NODES[0][1]).update(
            nodegroup_id=REFERENCE_NODES[0][0]
        )
        with self.assertRaisesMessage(
            ImproperlyConfigured, "misplaced nodes in monument"
        ):
            self.query_names([uuid4()])

    def test_wrong_datatype_raises_configuration_error(self):
        Node.objects.filter(pk=REFERENCE_NODES[0][1]).update(datatype="string")
        with self.assertRaisesMessage(ImproperlyConfigured, "primary_reference_number"):
            self.query_names([uuid4()])

    def test_misplaced_grouping_node_raises_configuration_error(self):
        Node.objects.filter(pk=NAME_NODES[0][0]).update(
            nodegroup_id=REFERENCE_NODES[0][0]
        )
        with self.assertRaisesMessage(
            ImproperlyConfigured, "misplaced nodes in monument"
        ):
            self.query_names([uuid4()])

    def test_duplicate_reference_tiles_are_not_collapsed_by_aqs_distinct(self):
        resource = self.resource()
        NodeGroup.objects.filter(pk=REFERENCE_NODES[0][0]).update(cardinality="n")
        self.reference(resource, 123)
        self.reference(resource, 123)
        self.assert_equivalent(
            [resource.pk], [(resource, "Monument 123"), (resource, "Monument 123")]
        )

    def test_duplicate_null_candidates_across_models_still_fall_back(self):
        resource = self.resource()
        self.name(resource, None)
        self.name(resource, None, model=1)
        self.reference(resource, 123)
        self.assert_equivalent([resource.pk], [(resource, "Monument 123")])

    def test_invalid_use_type_is_not_hidden_by_multiple_primary_candidates(self):
        resource = self.resource()
        self.name(resource, "First")
        self.name(resource, "Second", model=1)
        self.name(resource, "Invalid", model=2, name_use_type="not-a-uuid")
        self.reference(resource, 123)
        with self.assertRaises(DataError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM hapi.get_heritage_asset_names(%s::uuid[])",
                    [[str(resource.pk)]],
                )
        with self.assertRaises(DataError), transaction.atomic():
            list(self.query_names([resource.pk]))

    def test_public_aqs_selects_all_six_configured_nodegroups(self):
        resource = self.resource()
        self.name(resource, "Named")
        self.reference(resource, 123)
        with patch.object(TileTree, "get_tiles", wraps=TileTree.get_tiles) as select:
            query = self.query_names([resource.pk])
        self.assertEqual(
            [call.args for call in select.call_args_list],
            [
                ("monument", "monument_names"),
                ("monument", "system_reference_numbers"),
                ("historic-aircraft", "names"),
                ("historic-aircraft", "system_reference_numbers"),
                ("maritime-vessel", "names"),
                ("maritime-vessel", "system_reference_numbers"),
            ],
        )
        for call in select.call_args_list:
            self.assertEqual(call.kwargs["depth"], 0)
            self.assertTrue(call.kwargs["nodes"])
            self.assertNotIn("resource_ids", call.kwargs)
        with self.assertNumQueries(1):
            self.assertEqual(
                list(query),
                [{"resourceinstanceid": resource.pk, "heritageassetname": "Named 123"}],
            )

    def test_requested_ids_are_bound_only_once(self):
        resource_ids = [uuid4(), uuid4()]
        _, parameters = self.query_names(resource_ids).query.sql_with_params()
        for resource_id in resource_ids:
            self.assertEqual(parameters.count(resource_id), 1)

    def test_aliases_work_after_value_node_uuids_change(self):
        resource = self.resource()
        name_tile = self.name(resource, "Named")
        self.reference(resource, 123)
        reference_tile = resource.tilemodel_set.get(nodegroup_id=REFERENCE_NODES[0][0])
        for tile in (name_tile, reference_tile):
            tile.data = {
                self.move_node(node_id): value for node_id, value in tile.data.items()
            }
            tile.save()
        self.assertEqual(
            list(self.query_names([resource.pk])),
            [{"resourceinstanceid": resource.pk, "heritageassetname": "Named 123"}],
        )

    @staticmethod
    def move_node(node_id):
        replacement = uuid4()
        Node.objects.filter(pk=node_id).update(nodeid=replacement)
        return str(replacement)
