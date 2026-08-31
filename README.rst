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
* Preserve every previously used final lot or serial in Device history after
  the active Final Lot is cleared or replaced.
* Derive the last customer, Sale Order, delivery, and customer order reference
  from completed stock moves.
* Update Device state automatically for completed sales, returns, and scrap
  operations.
* Link standard Unbuild Orders to Registry Devices and validate physical
  identity before disassembly.
* Link existing Registry Devices to Manufacturing Orders and validate the
  physical Product Form transition against the BoM.
* Synchronize untracked manufacturing results back to linked Registry Devices.
* Prepare tracked output serials from Device UID and synchronize the completed
  result back to its Registry Device.
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

On each Product Form, configure Device State after Manufacturing. ``Do Not
Change`` updates the Product Form and location while preserving the Device's
existing lifecycle state.

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

Current Final Lot / Serial represents only the active serial of the current
retail form. Clearing it keeps the Odoo lot, its Product, stock traceability,
and sales metadata in the Device's Final Lot History.

Select multiple records in the Device list and use Batch Update to change only
the selected fields together.

Manufacturing Orders
~~~~~~~~~~~~~~~~~~~~

Open ``Manufacturing -> Operations -> Manufacturing Orders`` and create the
normal Odoo order with its Product, BoM, and quantity. Use the Device Registry
tab to link the existing physical Devices represented by this order. Leaving
the field empty preserves the standard Odoo manufacturing workflow.

A linked order must use one Device Type, produce one of its allowed Product
Forms, and consume exactly one common source Product Form per Device. The order
quantity, source component quantity, and number of linked Devices must match.
Each Device must currently have that source Product Form, be outside a Bundle,
and not be Reserved, Sold, Scrapped, or linked to another active order.
Serial-tracked outputs require a separate quantity-one order for each Device;
untracked intermediate forms may use batch orders.

Completing a linked order with an untracked output updates Current Product /
Current Form and Odoo Location on every Device. It applies the configured
Device State after Manufacturing, or preserves the current state when ``Do Not
Change`` is selected. Quality Status is deliberately preserved because a
completed Manufacturing Order does not prove that a separate quality check has
passed.

All linked Devices in an untracked batch must be completed together. Partial
production and backorders are rejected because the order cannot otherwise
identify which Device UIDs were completed. An active Final Lot must be cleared
before producing another untracked form. Device and Manufacturing Order chatter
record the synchronization.

Tracked outputs are validated as quantity-one orders. On confirmation, the
addon creates a Product serial named exactly like Device UID or safely reuses a
matching existing serial. A manually selected conflicting serial, Tracking by
Lots, an on-hand serial, or an uncompensated previously produced serial is
rejected. The prepared serial is recorded in Manufacturing Order chatter.

After successful completion, the addon verifies that exactly one positive
output move line used the prepared serial. It then updates Current Product /
Current Form, the configured Device State, the actual output destination
location, and Current Final Lot / Serial. Quality Status remains unchanged.
The serial is permanently linked to the Device through Final Lot History, and
both records receive audit messages.

Unbuild Orders
~~~~~~~~~~~~~~

Open ``Manufacturing -> Operations -> Unbuild Orders`` and select the Product
being disassembled. For a tracked final Product, selecting its current Lot /
Serial Number automatically identifies the Registry Device. For an untracked
intermediate Product Form, select the Registry Device manually.

A registry-managed Unbuild Order must process exactly one Device. Its Product
must match Current Product / Current Form, the selected final lot must be the
Device's current Final Lot, and the Device must first be released from any
Bundle with Cancel and Unpair.

After a successful Unbuild, the addon finds exactly one resulting Product Form
allowed for the Device Type. It updates Current Product / Current Form, sets
State to Rework and Quality Status to Needs Test, records the destination
location, and clears only the active Final Lot while preserving its history.
The operation is rejected if the BoM produces no unique next Product Form.

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
