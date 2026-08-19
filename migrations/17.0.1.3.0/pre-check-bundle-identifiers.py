def migrate(cr, version):
    cr.execute(
        """
        SELECT name
          FROM pinout_device_bundle
         GROUP BY name
        HAVING COUNT(*) > 1
         ORDER BY name
         LIMIT 10
        """
    )
    duplicate_names = [name for (name,) in cr.fetchall()]
    if duplicate_names:
        raise RuntimeError(
            "Bundle IDs must be unique before upgrading pinout_device_registry. "
            f"Resolve duplicate names first: {', '.join(duplicate_names)}"
        )
