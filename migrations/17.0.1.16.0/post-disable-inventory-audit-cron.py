from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    # The legacy record is noupdate, so XML alone cannot disable it on upgrade.
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref(
        "pinout_device_registry.inventory_audit_refresh_cron", raise_if_not_found=False
    )
    if cron:
        cron.write({"active": False})
