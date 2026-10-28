"""Alias-first AQS implementation of the heritage asset name query."""

from uuid import UUID

from arches.app.models.models import Node
from arches_querysets.models import TileTree
from django.core.exceptions import ImproperlyConfigured
from django.db.models import (
    Case,
    Count,
    F,
    Func,
    Min,
    Q,
    QuerySet,
    Subquery,
    TextField,
    UUIDField,
    Value,
    When,
    Window,
)
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import Cast, Coalesce, Left, Length, NullIf, Trim

from mariner_app.heritage_asset_names import PRIMARY_NAME_TYPE


# AQS: graph slugs and configured aliases replace hard-coded graph/node UUIDs.
GRAPH_ALIASES = (
    ("monument", "monument_names", "monument_name", "monument_name_use_type"),
    ("historic-aircraft", "names", "name", "name_use_type"),
    ("maritime-vessel", "names", "name", "name_use_type"),
)
REFERENCE_GROUP = "system_reference_numbers"
REFERENCE_NODE = "primary_reference_number"


def get_heritage_asset_names(
    resource_ids: list[UUID | str], *, language_code: str = "en"
) -> QuerySet:
    """Return the same UUID/text dictionaries as the SQL and UUID-based versions.

    Requires the configured graph, nodegroup, and node aliases. Data evaluation
    remains one SQL statement; graph/node metadata is loaded during construction.
    """
    # Django: validate configuration in one metadata query. This also supplies
    # AQS's documented `nodes` argument instead of rediscovering nodes six times.
    aliases = {REFERENCE_GROUP, REFERENCE_NODE}
    for _, group, name, use_type in GRAPH_ALIASES:
        aliases.update((group, name, use_type))
    nodes = list(
        Node.objects.filter(
            graph__slug__in=[slug for slug, *_ in GRAPH_ALIASES],
            alias__in=aliases,
        ).select_related("graph", "nodegroup")
    )
    nodes_by_graph = {
        slug: {node.alias: node for node in nodes if node.graph.slug == slug}
        for slug, *_ in GRAPH_ALIASES
    }

    selections = []
    use_types = {}
    candidates = []
    candidate_names = []
    reference_values = []
    reference_groups = []
    for slug, group, name, use_type in GRAPH_ALIASES:
        graph_nodes = nodes_by_graph[slug]
        required = {
            group: "semantic",
            name: "string",
            use_type: "concept",
            REFERENCE_GROUP: "semantic",
            REFERENCE_NODE: "number",
        }
        for alias, datatype in required.items():
            node = graph_nodes.get(alias)
            if node is None or node.datatype != datatype:
                raise ImproperlyConfigured(
                    f"AQS heritage asset configuration: {slug}.{alias} "
                    f"must exist with datatype {datatype}."
                )
        if (
            graph_nodes[group].nodegroup_id != graph_nodes[group].pk
            or graph_nodes[REFERENCE_GROUP].nodegroup_id
            != graph_nodes[REFERENCE_GROUP].pk
            or graph_nodes[name].nodegroup_id != graph_nodes[group].pk
            or graph_nodes[use_type].nodegroup_id != graph_nodes[group].pk
            or graph_nodes[REFERENCE_NODE].nodegroup_id
            != graph_nodes[REFERENCE_GROUP].pk
        ):
            raise ImproperlyConfigured(
                f"AQS heritage asset configuration: misplaced nodes in {slug}."
            )
        value_nodes = [graph_nodes[alias] for alias in (name, use_type, REFERENCE_NODE)]

        # AQS: select the name and reference nodegroups by alias. depth=0 and
        # values() avoid materializing/prefetching complete tile trees. Project
        # tile IDs, so AQS's DISTINCT cannot collapse identical tiles.
        for nodegroup_alias in (group, REFERENCE_GROUP):
            selections.append(
                TileTree.get_tiles(slug, nodegroup_alias, nodes=value_nodes, depth=0)
                # Django: anchor the alias-resolved group for indexed tile lookups.
                .filter(nodegroup_id=graph_nodes[nodegroup_alias].pk)
                .order_by()
                .values("tileid")
            )

        # Django: read node values by their alias-resolved node IDs. The tiles
        # of all three graphs share one queryset, where AQS's per-graph value
        # aliases would collide. This also preserves PostgreSQL UUID casting
        # and explicit-language extraction; AQS's any-language lookups would
        # change the selected-language semantics.
        use_type_alias = f"_use_type_{len(use_types)}"
        use_types[use_type_alias] = Cast(
            KeyTextTransform(str(graph_nodes[use_type].pk), "data"), UUIDField()
        )
        candidate = Q(nodegroup_id=graph_nodes[group].pk) & Q(
            **{use_type_alias: PRIMARY_NAME_TYPE}
        )
        candidates.append(When(candidate, then=F("pk")))
        candidate_names.append(
            When(
                candidate,
                then=Trim(
                    KeyTextTransform(
                        "value",
                        KeyTransform(
                            language_code,
                            KeyTransform(str(graph_nodes[name].pk), "data"),
                        ),
                    )
                ),
            )
        )
        # Django: exact JSON text intentionally bypasses AQS's number-to-float
        # conversion, retaining large integers and legacy textual identifiers.
        reference_values.append(
            When(
                nodegroup_id=graph_nodes[REFERENCE_GROUP].pk,
                then=KeyTextTransform(str(graph_nodes[REFERENCE_NODE].pk), "data"),
            )
        )
        reference_groups.append(graph_nodes[REFERENCE_GROUP].pk)

    # Django: OR combines AQS selections across every name group, including
    # cross-model tiles. A tile-ID semijoin keeps every physical tile exactly
    # once, and binds the requested IDs only once.
    selected = selections[0]
    for selection in selections[1:]:
        selected = selected | selection
    tiles = TileTree.objects.filter(
        pk__in=Subquery(selected.filter(resourceinstance_id__in=resource_ids))
    ).alias(**use_types)

    # Django: window functions over each resource's name and reference tiles
    # count and pick primary-name candidates in one set-based pass. Correlated
    # subqueries would rescan every resource's tiles once per reference row.
    # Count physical tiles, not projected names, and evaluate every candidate
    # so an invalid UUID still raises.
    per_resource = {"partition_by": [F("resourceinstance_id")]}
    tiles = (
        tiles.alias(
            _candidate_count=Window(Count(Case(*candidates)), **per_resource),
            # Min only decides when there is exactly one candidate.
            _candidate_name=Window(
                Min(Case(*candidate_names, output_field=TextField())),
                **per_resource,
            ),
            _reference=Case(*reference_values, output_field=TextField()),
            _resource_type=KeyTextTransform(
                language_code, "resourceinstance__graph__name"
            ),
        )
        .alias(
            _nonblank_reference=NullIf(
                Trim("_reference"), Value(""), output_field=TextField()
            ),
        )
        .alias(
            # Django: a filter that mentions a window applies after the window
            # (an outer QUALIFY query); a plain WHERE would drop the name tiles
            # before they are counted. The count is never NULL, so this marks
            # result rows: reference tiles with a nonblank reference.
            _result_row=Case(
                When(
                    Q(nodegroup_id__in=reference_groups)
                    & Q(_nonblank_reference__isnull=False),
                    then=F("_candidate_count"),
                )
            ),
            # Django: Coalesce preserves blank names, which are distinct from NULL.
            _label=Coalesce(
                Case(
                    When(_candidate_count=1, then=F("_candidate_name")),
                    output_field=TextField(),
                ),
                Case(
                    When(
                        _resource_type__endswith="s",
                        then=Left("_resource_type", Length("_resource_type") - 1),
                    ),
                    default=F("_resource_type"),
                    output_field=TextField(),
                ),
                output_field=TextField(),
            ),
        )
    )
    # Django: PostgreSQL || propagates NULL; Django Concat would replace it by ''.
    return (
        tiles.filter(_result_row__isnull=False)
        .annotate(
            resourceinstanceid=F("resourceinstance_id"),
            heritageassetname=Func(
                F("_label"),
                Value(" "),
                F("_reference"),
                function="",
                template="(%(expressions)s)",
                arg_joiner=" || ",
                output_field=TextField(),
            ),
        )
        .order_by("resourceinstanceid")
        .values("resourceinstanceid", "heritageassetname")
    )
