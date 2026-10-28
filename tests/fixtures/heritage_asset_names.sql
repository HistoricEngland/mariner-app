CREATE SCHEMA IF NOT EXISTS hapi;

-- Select localized asset/graph names; Primary is a Name Use Type value.
CREATE OR REPLACE FUNCTION hapi.get_heritage_asset_names(p_ids uuid[], p_language text DEFAULT 'en')
RETURNS TABLE (resourceinstanceid uuid, heritageassetname text)
LANGUAGE sql STABLE PARALLEL SAFE
AS $function$
    WITH requested_resources AS (
        SELECT DISTINCT unnest(p_ids) AS resourceinstanceid
    ), resource_types AS (
        SELECT rr.resourceinstanceid,
               g.name ->> p_language AS resource_type
        FROM requested_resources rr
        JOIN public.resource_instances ri ON ri.resourceinstanceid = rr.resourceinstanceid
        JOIN public.graphs g ON g.graphid = ri.graphid
    ), primary_name_candidates AS (
        SELECT rr.resourceinstanceid,
               trim(t.tiledata->'676d47ff-9c1c-11ea-b07f-f875a44e0e11'->p_language->>'value') AS resource_name
        FROM requested_resources rr
        JOIN public.tiles t ON t.resourceinstanceid = rr.resourceinstanceid
        WHERE t.nodegroupid = '676d47f9-9c1c-11ea-9aa0-f875a44e0e11'
          AND (t.tiledata->>'676d47fc-9c1c-11ea-b5b0-f875a44e0e11')::uuid = '2df285fa-9cf2-45e7-bc05-a67b7d7ddc2f'
        UNION ALL
        SELECT rr.resourceinstanceid,
               trim(t.tiledata->'008e66d1-28e0-11eb-b03a-f875a44e0e11'->p_language->>'value') AS resource_name
        FROM requested_resources rr
        JOIN public.tiles t ON t.resourceinstanceid = rr.resourceinstanceid
        WHERE t.nodegroupid = '008e66cc-28e0-11eb-85dc-f875a44e0e11'
          AND (t.tiledata->>'008e66cf-28e0-11eb-b758-f875a44e0e11')::uuid = '2df285fa-9cf2-45e7-bc05-a67b7d7ddc2f'
        UNION ALL
        SELECT rr.resourceinstanceid,
               trim(t.tiledata->'d00d4c8c-299f-11eb-bc0e-f875a44e0e11'->p_language->>'value') AS resource_name
        FROM requested_resources rr
        JOIN public.tiles t ON t.resourceinstanceid = rr.resourceinstanceid
        WHERE t.nodegroupid = 'd00d4c87-299f-11eb-b05e-f875a44e0e11'
          AND (t.tiledata->>'d00d4c8a-299f-11eb-9403-f875a44e0e11')::uuid = '2df285fa-9cf2-45e7-bc05-a67b7d7ddc2f'
    ), primary_names AS (
        SELECT resourceinstanceid, min(resource_name) AS resource_name
        FROM primary_name_candidates
        GROUP BY resourceinstanceid
        HAVING count(*) = 1
    ), primary_reference_numbers AS (
        SELECT rr.resourceinstanceid,
               t.tiledata->>'2182c97e-b532-11ee-b6dc-0242ac120006' AS primary_reference_number
        FROM requested_resources rr
        JOIN public.tiles t ON t.resourceinstanceid = rr.resourceinstanceid
        WHERE t.nodegroupid = '2182ba1a-b532-11ee-b6dc-0242ac120006'
        UNION ALL
        SELECT rr.resourceinstanceid,
               t.tiledata->>'95495b17-298b-11eb-b6cb-f875a44e0e11' AS primary_reference_number
        FROM requested_resources rr
        JOIN public.tiles t ON t.resourceinstanceid = rr.resourceinstanceid
        WHERE t.nodegroupid = '95495b13-298b-11eb-b746-f875a44e0e11'
        UNION ALL
        SELECT rr.resourceinstanceid,
               t.tiledata->>'f75ac300-29a7-11eb-b7b9-f875a44e0e11' AS primary_reference_number
        FROM requested_resources rr
        JOIN public.tiles t ON t.resourceinstanceid = rr.resourceinstanceid
        WHERE t.nodegroupid = 'f75a9ba3-29a7-11eb-aad5-f875a44e0e11'
    )
    SELECT rt.resourceinstanceid,
           CASE
               WHEN pn.resource_name IS NOT NULL
                   THEN pn.resource_name || ' ' || prn.primary_reference_number
               WHEN rt.resource_type LIKE '%s'
                   THEN left(rt.resource_type, length(rt.resource_type) - 1) || ' ' || prn.primary_reference_number
               ELSE rt.resource_type || ' ' || prn.primary_reference_number
           END AS heritageassetname
    FROM resource_types rt
    JOIN primary_reference_numbers prn ON prn.resourceinstanceid = rt.resourceinstanceid
    LEFT JOIN primary_names pn ON pn.resourceinstanceid = rt.resourceinstanceid
    WHERE NULLIF(btrim(prn.primary_reference_number), '') IS NOT NULL
    ORDER BY rt.resourceinstanceid;
$function$;
