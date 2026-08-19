from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    dual_type = env.ref("pinout_device_registry.bundle_type_dual")
    other_type = env.ref("pinout_device_registry.bundle_type_other")

    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = 'pinout_device_bundle'
           AND column_name = 'bundle_type'
        """
    )
    if cr.fetchone():
        cr.execute(
            """
            UPDATE pinout_device_bundle
               SET bundle_type_id = CASE
                   WHEN bundle_type = 'dual' THEN %s
                   ELSE %s
               END
             WHERE bundle_type_id IS NULL
            """,
            [dual_type.id, other_type.id],
        )
        cr.execute("ALTER TABLE pinout_device_bundle DROP COLUMN bundle_type")
    else:
        cr.execute(
            """
            UPDATE pinout_device_bundle
               SET bundle_type_id = %s
             WHERE bundle_type_id IS NULL
            """,
            [dual_type.id],
        )

    cr.execute(
        "ALTER TABLE pinout_device_bundle ALTER COLUMN bundle_type_id SET NOT NULL"
    )
