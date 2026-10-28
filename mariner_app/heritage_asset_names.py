"""Heritage asset labels for the Mariner resource models."""

from uuid import UUID

from arches.app.models.models import Node
from arches_querysets.models import TileTree
from arches_querysets.utils.models import get_node_value_expression
from django.core.exceptions import ImproperlyConfigured
from django.db.models import (
    Case,
    Count,
    F,
    Func,
    Min,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    TextField,
    UUIDField,
    Value,
    When,
)
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import Cast, Left, Length, NullIf, Trim


PRIMARY_NAME_TYPE = UUID("2df285fa-9cf2-45e7-bc05-a67b7d7ddc2f")

# (nodegroup, localized name node, name-use-type node)
NAME_NODES = (
    (
        "676d47f9-9c1c-11ea-9aa0-f875a44e0e11",
        "676d47ff-9c1c-11ea-b07f-f875a44e0e11",
        "676d47fc-9c1c-11ea-b5b0-f875a44e0e11",
    ),
    (
        "008e66cc-28e0-11eb-85dc-f875a44e0e11",
        "008e66d1-28e0-11eb-b03a-f875a44e0e11",
        "008e66cf-28e0-11eb-b758-f875a44e0e11",
    ),
    (
        "d00d4c87-299f-11eb-b05e-f875a44e0e11",
        "d00d4c8c-299f-11eb-bc0e-f875a44e0e11",
        "d00d4c8a-299f-11eb-9403-f875a44e0e11",
    ),
)

# (nodegroup, numeric reference-number node)
REFERENCE_NODES = (
    (
        "2182ba1a-b532-11ee-b6dc-0242ac120006",
        "2182c97e-b532-11ee-b6dc-0242ac120006",
    ),
    (
        "95495b13-298b-11eb-b746-f875a44e0e11",
        "95495b17-298b-11eb-b6cb-f875a44e0e11",
    ),
    (
        "f75a9ba3-29a7-11eb-aad5-f875a44e0e11",
        "f75ac300-29a7-11eb-b7b9-f875a44e0e11",
    ),
)


def get_heritage_asset_names(
    resource_ids: list[UUID | str], *, language_code: str = "en"
) -> QuerySet:
    """Return dictionaries with resourceinstanceid and heritageassetname.

    Uses localized string names, concept name-use types, and numeric references.
    Asset and graph names use the selected language, without language fallback.
    Duplicate reference tiles intentionally produce duplicate result rows.
    """
    node_ids = {
        UUID(node_id)
        for _, *node_ids_in_group in (*NAME_NODES, *REFERENCE_NODES)
        for node_id in node_ids_in_group
    }
    nodes = list(Node.objects.filter(pk__in=node_ids))
    missing = node_ids - {node.pk for node in nodes}
    if missing:
        raise ImproperlyConfigured(
            "Missing Mariner heritage asset nodes: "
            + ", ".join(sorted(map(str, missing)))
        )
    reference_node_ids = {node for _, node in REFERENCE_NODES}
    alias_by_id = {str(node.pk): "_heritage_node_" + node.pk.hex for node in nodes}
    # Use AQS's tile-level expressions, not resource-level arrays or a
    # graph-specific get_tiles(): candidates must be counted across all three models.
    # Namespace aliases because different graphs may reuse the same node alias.
    tiles = TileTree.objects.alias(
        **{
            alias_by_id[str(node.pk)]: (
                # AQS numbers cast to float; identifiers must retain the exact
                # JSON text, including integers beyond float's exact range.
                KeyTextTransform(str(node.pk), "data")
                if str(node.pk) in reference_node_ids
                else get_node_value_expression(node, many=False)
            )
            for node in nodes
        }
    ).order_by()
    name_conditions = Q()
    name_cases = []
    for nodegroup, name_node, use_type_node in NAME_NODES:
        type_alias = "_heritage_use_type_" + UUID(use_type_node).hex
        tiles = tiles.alias(
            **{type_alias: Cast(F(alias_by_id[use_type_node]), UUIDField())}
        )
        condition = Q(nodegroup_id=nodegroup, **{type_alias: PRIMARY_NAME_TYPE})
        name_conditions |= condition
        name_cases.append(
            When(
                condition,
                then=Trim(
                    KeyTextTransform(
                        "value", KeyTransform(language_code, F(alias_by_id[name_node]))
                    )
                ),
            )
        )

    primary_names = (
        tiles.filter(
            name_conditions, resourceinstance_id=OuterRef("resourceinstance_id")
        )
        .values("resourceinstance_id")
        .annotate(
            candidate_count=Count("pk"),
            resource_name=Min(Case(*name_cases, output_field=TextField())),
        )
        .filter(candidate_count=1)
        .values("resource_name")
    )
    references = tiles.filter(
        resourceinstance_id__in=resource_ids,
        nodegroup_id__in=[nodegroup for nodegroup, _ in REFERENCE_NODES],
    ).alias(
        primary_reference_number=Case(
            *(
                When(nodegroup_id=nodegroup, then=F(alias_by_id[node]))
                for nodegroup, node in REFERENCE_NODES
            ),
            output_field=TextField(),
        ),
        resource_type=KeyTextTransform(language_code, "resourceinstance__graph__name"),
        primary_name=Subquery(primary_names, output_field=TextField()),
    )
    references = references.alias(
        nonblank_reference=NullIf(
            Trim(F("primary_reference_number")), Value(""), output_field=TextField()
        ),
        label=Case(
            When(primary_name__isnull=False, then=F("primary_name")),
            When(
                resource_type__endswith="s",
                then=Left(F("resource_type"), Length(F("resource_type")) - 1),
            ),
            default=F("resource_type"),
            output_field=TextField(),
        ),
    )
    # Django Concat coalesces NULL to ''; PostgreSQL || must propagate NULL.
    return (
        references.filter(nonblank_reference__isnull=False)
        .annotate(
            resourceinstanceid=F("resourceinstance_id"),
            heritageassetname=Func(
                F("label"),
                Value(" "),
                F("primary_reference_number"),
                function="",
                template="(%(expressions)s)",
                arg_joiner=" || ",
                output_field=TextField(),
            ),
        )
        .order_by("resourceinstanceid")
        .values("resourceinstanceid", "heritageassetname")
    )
