import os
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from arches.app.models.models import (
    GraphModel,
    Node,
    NodeGroup,
    ResourceInstance,
    TileModel,
)
from django.core.exceptions import ImproperlyConfigured
from django.db import DataError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from mariner_app.heritage_asset_names import (
    NAME_NODES,
    PRIMARY_NAME_TYPE,
    REFERENCE_NODES,
    get_heritage_asset_names,
)
from tests.heritage_asset_names_report import (
    Comparison,
    callable_source,
    sql_source,
    write_report,
)
from tests.heritage_asset_names_benchmark import measure_requests
from tests.benchmark_heritage_asset_names import eligible_resource_ids


class HeritageAssetNamesTests(TestCase):
    query_names = staticmethod(get_heritage_asset_names)
    report_stem = "heritage_asset_names"
    missing_node_message = "Missing Mariner heritage asset nodes"
    graph_slugs = ("monument", "historic-aircraft", "maritime-vessel")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.comparisons = []
        benchmark_mode = os.environ.get("HERITAGE_ASSET_NAMES_BENCHMARK", "0")
        if benchmark_mode not in ("0", "1"):
            raise ValueError("HERITAGE_ASSET_NAMES_BENCHMARK must be 0 or 1.")
        cls.benchmark = benchmark_mode == "1"
        report_path = Path(
            os.environ.get(
                "HERITAGE_ASSET_NAMES_REPORT",
                Path(__file__).parent
                / (
                    cls.report_stem + "_benchmark.md"
                    if cls.benchmark
                    else cls.report_stem + "_report.md"
                ),
            )
        )
        cls.addClassCleanup(
            write_report,
            report_path,
            cls.comparisons,
            implementations=[
                sql_source(
                    "SQL",
                    "hapi.get_heritage_asset_names(), installed in the test database",
                ),
                callable_source("AQS", cls.query_names),
            ],
        )

    models = (
        ("076f9381-7b00-11e9-8d6b-80000b44d1d9", "Monument"),
        ("b8032b00-594d-11e9-9cf0-18cf5eb368c4", "Historic Aircraft"),
        ("49bac32e-5464-11e9-a6e2-000d3ab1e588", "Maritime Vessel"),
    )
    name_type_nodes = (
        "676d47fe-9c1c-11ea-aa28-f875a44e0e11",
        "008e66d5-28e0-11eb-9606-f875a44e0e11",
        "d00d4c90-299f-11eb-9c2f-f875a44e0e11",
    )

    @classmethod
    def setUpTestData(cls):
        with connection.cursor() as cursor:
            cursor.execute(
                (
                    Path(__file__).parent / "fixtures" / "heritage_asset_names.sql"
                ).read_text()
            )
        cls.graphs = []
        for index, (graph_id, name) in enumerate(cls.models):
            graph = GraphModel.objects.create(
                graphid=graph_id,
                slug=cls.graph_slugs[index],
                name={"en": name, "cy": "Adeiladau"},
                isresource=True,
            )
            cls.graphs.append(graph)
            name_group, name_node, use_type_node = NAME_NODES[index]
            reference_group, reference_node = REFERENCE_NODES[index]
            NodeGroup.objects.create(nodegroupid=name_group, cardinality="n")
            NodeGroup.objects.create(nodegroupid=reference_group, cardinality="1")
            prefix = "monument_" if index == 0 else ""
            for node_id, group_id, alias, datatype in (
                (name_group, name_group, prefix + "names", "semantic"),
                (
                    reference_group,
                    reference_group,
                    "system_reference_numbers",
                    "semantic",
                ),
                (name_node, name_group, prefix + "name", "string"),
                (use_type_node, name_group, prefix + "name_use_type", "concept"),
                (
                    cls.name_type_nodes[index],
                    name_group,
                    prefix + "name_type",
                    "concept",
                ),
                (
                    reference_node,
                    reference_group,
                    "primary_reference_number",
                    "number",
                ),
            ):
                Node.objects.create(
                    nodeid=node_id,
                    nodegroup_id=group_id,
                    graph=graph,
                    name=alias,
                    alias=alias,
                    datatype=datatype,
                    istopnode=False,
                )

    def resource(self, model=0):
        resource = ResourceInstance(
            resourceinstanceid=uuid4(), graph=self.graphs[model]
        )
        ResourceInstance.objects.bulk_create([resource])
        return resource

    def name(self, resource, value, *, model=0, name_use_type=PRIMARY_NAME_TYPE):
        group, name_node, use_type_node = NAME_NODES[model]
        if value is not None and not isinstance(value, dict):
            value = {"en": {"value": value, "direction": "ltr"}}
        return TileModel.objects.create(
            resourceinstance=resource,
            nodegroup_id=group,
            data={
                name_node: value,
                use_type_node: str(name_use_type) if name_use_type else None,
            },
        )

    def reference(self, resource, value, *, model=0):
        group, node = REFERENCE_NODES[model]
        TileModel.objects.create(
            resourceinstance=resource, nodegroup_id=group, data={node: value}
        )

    def assert_equivalent(self, resource_ids, expected, *, language_code="en"):
        columns = []

        def sql_request():
            nonlocal columns
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT resourceinstanceid, heritageassetname "
                    "FROM hapi.get_heritage_asset_names(%s::uuid[], %s)",
                    [[str(resource_id) for resource_id in resource_ids], language_code],
                )
                columns = [column.name for column in cursor.description]
                return [
                    {"resourceinstanceid": resource_id, "heritageassetname": label}
                    for resource_id, label in cursor.fetchall()
                ]

        def aqs_request():
            # Rebuild and evaluate every time; never reuse QuerySet result caches.
            return list(self.query_names(resource_ids, language_code=language_code))

        start = perf_counter()
        sql_rows = sql_request()
        sql_ms = (perf_counter() - start) * 1000
        with CaptureQueriesContext(connection) as queries:
            start = perf_counter()
            queryset = self.query_names(resource_ids, language_code=language_code)
            with self.assertNumQueries(1 if resource_ids else 0):
                aqs_rows = list(queryset)
            aqs_ms = (perf_counter() - start) * 1000
        self.comparisons.append(
            Comparison(
                test=self._testMethodName,
                language=language_code,
                requested_ids=[UUID(str(resource_id)) for resource_id in resource_ids],
                sql_rows=[
                    (row["resourceinstanceid"], row["heritageassetname"])
                    for row in sql_rows
                ],
                aqs_rows=[
                    (row["resourceinstanceid"], row["heritageassetname"])
                    for row in aqs_rows
                ],
                sql_ms=sql_ms,
                aqs_ms=aqs_ms,
            )
        )
        self.assertEqual(columns, ["resourceinstanceid", "heritageassetname"])
        # AQS loads node/datatype metadata separately; label data stays in one query.
        self.assertTrue(
            all(query["sql"].lstrip().startswith("SELECT") for query in queries)
        )
        expected_rows = [
            {"resourceinstanceid": resource.pk, "heritageassetname": label}
            for resource, label in expected
        ]

        def validate(sql_results, aqs_results):
            self.assertEqual(columns, ["resourceinstanceid", "heritageassetname"])
            self.assert_result_rows(sql_results, expected_rows)
            self.assert_result_rows(aqs_results, expected_rows)
            self.assertCountEqual(aqs_results, sql_results)

        validate(sql_rows, aqs_rows)
        if self.benchmark:
            measurements = measure_requests(sql_request, aqs_request, validate)
            comparison = self.comparisons[-1]
            comparison.sql_samples = measurements.sql.samples
            comparison.aqs_samples = measurements.aqs.samples
            comparison.warmups = measurements.warmups
        return aqs_rows

    def assert_result_rows(self, rows, expected_rows):
        self.assertCountEqual(rows, expected_rows)
        if rows:
            ids = [row["resourceinstanceid"] for row in rows]
            self.assertEqual(ids, sorted(ids))
            for row in rows:
                self.assertEqual(set(row), {"resourceinstanceid", "heritageassetname"})
                self.assertIsInstance(row["resourceinstanceid"], UUID)
                if row["heritageassetname"] is not None:
                    self.assertIsInstance(row["heritageassetname"], str)

    def test_primary_name_for_each_model(self):
        resources = [self.resource(model) for model in range(3)]
        for model, resource in enumerate(resources):
            self.name(resource, "  Named asset  ", model=model)
            self.reference(resource, 1000 + model, model=model)
        self.assert_equivalent(
            [resource.pk for resource in reversed(resources)],
            [
                (resource, f"Named asset {1000 + model}")
                for model, resource in enumerate(resources)
            ],
        )

    def test_missing_primary_name_uses_graph_name(self):
        resources = [self.resource(model) for model in range(3)]
        for model, resource in enumerate(resources):
            self.name(resource, "Secondary", model=model, name_use_type=uuid4())
            self.reference(resource, 123, model=model)
        self.assert_equivalent(
            [resource.pk for resource in resources],
            list(
                zip(
                    resources,
                    ("Monument 123", "Historic Aircraft 123", "Maritime Vessel 123"),
                )
            ),
        )

    def test_duplicate_primary_names_fall_back_even_when_identical(self):
        resource = self.resource()
        self.name(resource, "Same")
        self.name(resource, "Same")
        self.reference(resource, 123)
        self.assert_equivalent([resource.pk], [(resource, "Monument 123")])

    def test_primary_names_are_counted_across_models(self):
        resource = self.resource()
        self.name(resource, "First")
        self.name(resource, "Second", model=1)
        self.reference(resource, 123)
        self.assert_equivalent([resource.pk], [(resource, "Monument 123")])

    def test_null_primary_name_still_counts_as_candidate(self):
        resource = self.resource()
        self.name(resource, None)
        self.name(resource, "Other")
        self.reference(resource, 123)
        self.assert_equivalent([resource.pk], [(resource, "Monument 123")])

    def test_single_null_primary_name_falls_back(self):
        resource = self.resource()
        self.name(resource, None)
        self.reference(resource, 123)
        self.assert_equivalent([resource.pk], [(resource, "Monument 123")])

    def test_empty_primary_name_does_not_fall_back(self):
        resource = self.resource()
        self.name(resource, "   ")
        self.reference(resource, 123)
        self.assert_equivalent([resource.pk], [(resource, " 123")])

    def test_nonprimary_names_do_not_count(self):
        resource = self.resource()
        self.name(resource, "Primary")
        self.name(resource, "Secondary", name_use_type=uuid4())
        self.name(resource, "Untyped", name_use_type=None)
        self.reference(resource, 123)
        self.assert_equivalent([resource.pk], [(resource, "Primary 123")])

    def test_primary_marker_is_read_from_name_use_type_not_name_type(self):
        for model, (_, graph_name) in enumerate(self.models):
            with self.subTest(model=graph_name):
                resource = self.resource(model)
                tile = self.name(resource, "Named", model=model, name_use_type=None)
                tile.data[self.name_type_nodes[model]] = str(PRIMARY_NAME_TYPE)
                TileModel.objects.filter(pk=tile.pk).update(data=tile.data)
                self.reference(resource, 123, model=model)
                self.assert_equivalent([resource.pk], [(resource, f"{graph_name} 123")])
                tile.data[NAME_NODES[model][2]] = str(PRIMARY_NAME_TYPE)
                tile.data[self.name_type_nodes[model]] = str(uuid4())
                TileModel.objects.filter(pk=tile.pk).update(data=tile.data)
                self.assert_equivalent([resource.pk], [(resource, "Named 123")])

    def test_missing_localized_name_paths_fall_back(self):
        for name_data in (
            {},
            {"fr": {"value": "French only", "direction": "ltr"}},
            {"en": {}},
            {"en": None},
            {"en": {"value": None, "direction": "ltr"}},
        ):
            with self.subTest(name_data=name_data):
                resource = self.resource()
                self.name(resource, name_data)
                self.reference(resource, 123)
                self.assert_equivalent([resource.pk], [(resource, "Monument 123")])
        resource = self.resource()
        tile = self.name(resource, "Removed")
        del tile.data[NAME_NODES[0][1]]
        TileModel.objects.filter(pk=tile.pk).update(data=tile.data)
        self.reference(resource, 123)
        self.assert_equivalent([resource.pk], [(resource, "Monument 123")])

    def test_missing_name_translation_still_counts_as_candidate(self):
        resource = self.resource()
        self.name(resource, {"fr": {"value": "French only", "direction": "ltr"}})
        self.name(resource, "English")
        self.reference(resource, 123)
        self.assert_equivalent([resource.pk], [(resource, "Monument 123")])

    def test_name_and_graph_use_the_same_explicit_language(self):
        for model in range(3):
            with self.subTest(model=model):
                resource = self.resource(model)
                self.name(
                    resource,
                    {
                        "en": {"value": " English name ", "direction": "ltr"},
                        "cy": {"value": " Welsh name ", "direction": "ltr"},
                    },
                    model=model,
                )
                self.reference(resource, 123, model=model)
                self.assert_equivalent([resource.pk], [(resource, "English name 123")])
                self.assert_equivalent(
                    [resource.pk], [(resource, "Welsh name 123")], language_code="cy"
                )

    def test_missing_name_translation_uses_graph_not_another_name_language(self):
        resource = self.resource()
        self.name(resource, "English name")
        self.reference(resource, 123)
        self.assert_equivalent(
            [resource.pk], [(resource, "Adeiladau 123")], language_code="cy"
        )

    def test_numeric_references_preserve_exact_json_text(self):
        cases = (
            (0, "0"),
            (-123, "-123"),
            (123.5, "123.5"),
            (9007199254740993, "9007199254740993"),
            (10**40, "10000000000000000000000000000000000000000"),
            (1e20, "100000000000000000000"),
            (1e-7, "0.0000001"),
        )
        for model in range(3):
            with self.subTest(model=model):
                resources = []
                for value, _ in cases:
                    resource = self.resource(model)
                    resources.append(resource)
                    self.name(resource, "Named", model=model)
                    self.reference(resource, value, model=model)
                self.assert_equivalent(
                    [resource.pk for resource in resources],
                    [
                        (resource, f"Named {text}")
                        for resource, (_, text) in zip(resources, cases)
                    ],
                )

    def test_legacy_text_reference_is_not_normalized_as_a_number(self):
        resource = self.resource()
        self.reference(resource, "00123")
        self.assert_equivalent([resource.pk], [(resource, "Monument 00123")])

    def test_missing_null_empty_and_space_only_references_are_excluded(self):
        resources = [self.resource() for _ in range(5)]
        for resource, value in zip(resources, (None, "", "   ")):
            self.reference(resource, value)
        group, _ = REFERENCE_NODES[0]
        TileModel.objects.create(
            resourceinstance=resources[3], nodegroup_id=group, data={}
        )
        self.assert_equivalent([resource.pk for resource in resources], [])

    def test_reference_whitespace_is_preserved_and_trim_only_removes_spaces(self):
        resource = self.resource()
        tab_resource = self.resource()
        self.name(resource, " \tName\t ")
        self.name(tab_resource, " \tName\t ")
        self.reference(resource, "  123  ")
        self.reference(tab_resource, "\t")
        self.assert_equivalent(
            [resource.pk, tab_resource.pk],
            [(resource, "\tName\t   123  "), (tab_resource, "\tName\t \t")],
        )

    def test_multiple_and_duplicate_references_across_nodegroups_are_preserved(self):
        resource = self.resource()
        self.reference(resource, 123)
        self.reference(resource, 123, model=1)
        self.reference(resource, 456, model=2)
        self.assert_equivalent(
            [resource.pk],
            [
                (resource, "Monument 123"),
                (resource, "Monument 123"),
                (resource, "Monument 456"),
            ],
        )

    def test_requested_ids_are_deduplicated_and_unknown_ids_ignored(self):
        resource = self.resource()
        unrequested = self.resource()
        self.reference(resource, 123)
        self.reference(unrequested, 456)
        self.assert_equivalent(
            [str(resource.pk), resource.pk, uuid4()], [(resource, "Monument 123")]
        )

    def test_empty_input(self):
        self.assert_equivalent([], [])

    def test_all_resource_selection_matches_supported_nonblank_reference_results(self):
        eligible = [self.resource(model) for model in range(3)]
        for model, resource in enumerate(eligible):
            self.reference(resource, 0 if model == 0 else 123, model=model)
        # A second supported reference group must not duplicate the requested ID.
        self.reference(eligible[0], 456, model=1)
        for reference in (None, "", "   "):
            resource = self.resource()
            self.reference(resource, reference)
        self.resource()  # No reference tile.
        other_graph = GraphModel.objects.create(
            graphid=uuid4(), name={"en": "Other model"}, isresource=True
        )
        other = ResourceInstance(resourceinstanceid=uuid4(), graph=other_graph)
        ResourceInstance.objects.bulk_create([other])
        self.reference(other, 123)
        ids = eligible_resource_ids()
        self.assertEqual(ids, sorted(resource.pk for resource in eligible))
        self.assert_equivalent(
            ids,
            [
                (eligible[0], "Monument 0"),
                (eligible[0], "Monument 456"),
                (eligible[1], "Historic Aircraft 123"),
                (eligible[2], "Maritime Vessel 123"),
            ],
        )

    def test_all_resource_selection_can_be_empty(self):
        self.assertEqual(eligible_resource_ids(), [])

    def test_all_resource_selection_requires_reference_nodes(self):
        Node.objects.filter(pk=REFERENCE_NODES[0][1]).delete()
        with self.assertRaisesMessage(
            ImproperlyConfigured, "Missing Mariner primary-reference nodes"
        ):
            eligible_resource_ids()

    def test_graph_language_is_explicit(self):
        resource = self.resource()
        self.reference(resource, 123)
        self.assert_equivalent(
            [resource.pk], [(resource, "Adeiladau 123")], language_code="cy"
        )

    def test_missing_graph_translation_propagates_null(self):
        resource = self.resource()
        self.reference(resource, 123)
        self.assert_equivalent([resource.pk], [(resource, None)], language_code="fr")
        self.name(resource, {"fr": {"value": "Named", "direction": "ltr"}})
        self.assert_equivalent(
            [resource.pk], [(resource, "Named 123")], language_code="fr"
        )

    def test_singularization_is_case_sensitive_and_removes_only_one_s(self):
        resource = self.resource()
        self.reference(resource, 123)
        for graph_name, expected in (
            ("Glass", "Glas 123"),
            ("ASSETS", "ASSETS 123"),
            ("s", " 123"),
        ):
            with self.subTest(graph_name=graph_name):
                GraphModel.objects.filter(pk=resource.graph_id).update(
                    name={"en": graph_name}
                )
                self.assert_equivalent([resource.pk], [(resource, expected)])

    def test_uuid_cast_accepts_alternative_uuid_spelling(self):
        resource = self.resource()
        self.name(resource, "Named", name_use_type=PRIMARY_NAME_TYPE.hex.upper())
        self.reference(resource, 123)
        self.assert_equivalent([resource.pk], [(resource, "Named 123")])

    def test_invalid_name_use_type_raises_in_both_implementations(self):
        resource = self.resource()
        self.name(resource, "Named", name_use_type="not-a-uuid")
        self.reference(resource, 123)
        with self.assertRaises(DataError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM hapi.get_heritage_asset_names(%s::uuid[])",
                    [[str(resource.pk)]],
                )
        with self.assertRaises(DataError), transaction.atomic():
            list(self.query_names([resource.pk]))

    def test_missing_mariner_nodes_raise_configuration_error(self):
        Node.objects.filter(pk=NAME_NODES[0][1]).delete()
        with self.assertRaisesMessage(ImproperlyConfigured, self.missing_node_message):
            self.query_names([uuid4()])

    def test_node_alias_requirements(self):
        resource = self.resource()
        self.name(resource, "Named")
        self.reference(resource, 123)
        for alias in (None, "name"):
            with self.subTest(alias=alias):
                Node.objects.filter(pk=NAME_NODES[0][1]).update(alias=alias)
                self.assert_equivalent([resource.pk], [(resource, "Named 123")])
