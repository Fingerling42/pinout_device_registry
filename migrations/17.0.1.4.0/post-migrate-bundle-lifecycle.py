def migrate(cr, version):
    cr.execute(
        """
        UPDATE pinout_device_bundle
           SET state = 'ready_for_sale',
               reservation_delivery_id = NULL
         WHERE state = 'reserved'
        """
    )
