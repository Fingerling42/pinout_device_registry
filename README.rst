Pinout Device Registry
======================

Odoo 17 addon for tracking physical device identity, current product form,
lifecycle, quality, bundles, and final serial linkage.

The registry complements Odoo Inventory and Manufacturing. Odoo remains the
source of truth for quantities, stock moves, Bills of Materials, Manufacturing
Orders, deliveries, and final lots or serial numbers.

Features
--------

* Track every physical device by a globally unique Device UID.
* Record its current product variant, lifecycle state, quality status, location,
  temporary mark, and Robonomics addresses.
* Configure allowed Product Forms and Variant Summary attributes for each
  Device Type.
* Link a finished retail unit to an Odoo lot or serial number without creating
  lots for every intermediate production stage.
* Require the Final Lot number to match Device UID and its Product to match
  Current Product / Current Form.
* Keep existing Final Lot links visible when identity fields become
  incompatible, so they cannot be lost silently.
* Derive the last customer, Sale Order, delivery, and customer order reference
  from completed stock moves.
* Update Device state automatically for completed sales, returns, and scrap
  operations.
* Update selected Device fields in batches.
* Define reusable Bundle Types with generated or manually entered Bundle IDs.
* Validate Bundle composition against the variant-specific active Kit BoM and
  its Apply on Variants rules.
* Track Bundle readiness, reservation, sale, partial return, full return, and
  cancellation.
* Configure visible columns in Device and Bundle lists, including creation and
  last modification dates.

Dependencies
------------

* base
* mail
* product
* stock
* mrp
* sale_stock
* `Pinout Product Variant Search <https://github.com/PinoutLTD/pinout_product_variant_search>`_

Installation
------------

Add this module and ``pinout_product_variant_search`` to an Odoo addons path,
update the Apps list, and install Pinout Device Registry.

Configuration
-------------

Open ``Pinout -> Device Registry -> Configuration``.

Create Device Types and select the Product Forms allowed for each type. Add the
product attributes that should appear in Variant Summary and configure Variant
Codes when short values such as ``BMGR / SML`` are useful.

Create Bundle Types and configure their code, allowed Bundle Product Forms, and
whether they require a Kit BoM. Kit products must have an active phantom BoM
with the correct Apply on Variants values on component lines.

Usage
-----

Devices
~~~~~~~

Open ``Pinout -> Device Registry -> Devices`` and create a record for each
physical device. Enter its Device UID and Device Type, then maintain Current
Product / Current Form, state, quality, and location as production progresses.

When the device becomes a finished retail unit, assign its Final Lot / Serial.
The serial number must exactly match Device UID and belong to the selected
Current Product. Completed deliveries, returns, and scrap operations update
unambiguous Device states automatically.

Select multiple records in the Device list and use Batch Update to change only
the selected fields together.

Bundles
~~~~~~~

Open ``Pinout -> Device Registry -> Bundles`` and create a Draft Bundle. Select
its Bundle Type and Bundle Product / Kit Variant, review Expected Components,
and use Manage Devices to assign compatible existing Devices.

Mark the Bundle Ready for Sale after its component checklist is complete and
all Devices have a Final Lot, Quality Status OK, and Device State Ready for
Sale. The physical pairing is then locked.

Assigning all component serial numbers to one outgoing delivery reserves the
Bundle automatically. Completing the delivery marks it Sold. Customer returns
change it to Partially Returned or Returned according to the returned Devices.

A Bundle is a logical physical pairing, not a separate Odoo stock unit or
package. The addon does not replace Inventory, Manufacturing Orders, Purchase
Orders, or stock quantities.

License
-------

Apache-2.0
