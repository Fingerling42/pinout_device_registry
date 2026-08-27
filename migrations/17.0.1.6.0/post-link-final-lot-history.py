def migrate(cr, version):
    cr.execute(
        """
        UPDATE stock_lot AS lot
           SET pinout_device_id = device.id
          FROM pinout_device AS device
         WHERE device.final_lot_id = lot.id
           AND lot.pinout_device_id IS NULL
        """
    )
