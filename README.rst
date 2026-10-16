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
* Create new Devices in batches from editable rows or pasted spreadsheet columns.
* Update selected Device fields in batches.
* Audit Registry Devices against Inventory, with exact serial checks, grouped
  untracked shortages, surplus reminders, and visible stale results.
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

For warehouse users, ``Configuration -> Audit Scope`` defines the current
company's audit scope. Select locations and enable Include Child Locations for
each root as needed. Only internal, non-scrap locations are checked; overlapping
roots do not duplicate quantities. No scope is selected automatically on install
or upgrade. Stockable Product Forms from active Device Types are checked,
including forms with stock but no registered Devices.

Usage
-----

Inventory Audit
~~~~~~~~~~~~~~~

Open ``Pinout -> Device Registry -> Inventory Audit`` to see discrepancies and
the number of groups with unregistered surplus. The Discrepancy Groups smart
button opens groups; remove its default Discrepancies filter to see all results.
Filters cover matches, shortages, surplus, freshness and errors, with links to Devices,
Products, and Stock Quants. Each group uses an exact Product Variant, Location,
and company; surplus in another group never compensates for a shortage.

Serial-tracked forms are checked against the current Final Lot, its Product,
company, quantity one, and exact location. Missing or inconsistent serial stock
gives Exact Mismatch. Missing Final Lot, lot tracking, incomplete metadata, or
locations outside the configured scope give Not Checked with a reason.
Historical Final Lots do not replace the current identity. Details show expected
and actual Product, serial, Location, company and quantities. Serial Stock Quants
opens the current and saved serial's quants across locations with normal access
rules. Equal group quantities do not prove that the expected serials match.

Negative balances for the expected Product and company in non-scrap production,
inventory, supplier or customer locations describe virtual operations and do not
alone cause Exact Mismatch. They remain visible in details and source access
checks. Positive stock elsewhere, negative internal/transit/scrap stock, and
nonzero balances with another Product or company still cause a mismatch.

Untracked forms compare the number of active Devices against physical quantity,
including reserved stock. Sold, Scrapped, and archived Devices are excluded.
Group Shortage applies to every Device in the group and cannot identify a
missing UID. Match confirms sufficient group quantity without proving the
presence of a particular untracked Device. Unregistered Surplus is a reminder
to investigate stock or registration; it does not mark existing Devices as wrong.

The Device's Inventory Audit tab shows status, details, last successful check,
and separate freshness: No result, Recheck required, or No marked changes.
No marked changes does not guarantee current stock. Inventory and Registry
changes mark affected results pending in the same transaction, without
recalculating them.

Device list audit filters use the same accessible saved results as the displayed
fields; filtering does not run a check. A missing result or an inaccessible
source is shown as Not Checked / No result. Check scope and source access before
requesting a new check; this state does not confirm a stock discrepancy.

Checks run only when requested: Refresh on a Device checks its current and
previous groups immediately; Refresh on the dashboard checks the company's
full scope, including stock without registered Devices. Scope changes require
another manual full check. There is no scheduled or daily recalculation; the
legacy scheduled action is inactive on installation and disabled on upgrade.

A full check preserves successfully checked groups when another group fails.
Failed groups retain their last successful snapshot as stale, with an error;
retry Refresh manually. The last full check date advances only after the whole
pass succeeds. Full check state distinguishes an unconfigured scope, a check
not performed, a required full pass, an incomplete pass and a completed pass.
A source change marks affected results without erasing the previous full success.
Discovery errors also preserve previous snapshots. A failed
selected-Device check rolls back that request. Refresh the page if the displayed
results from another open tab need to be updated. Manual Refresh reloads the
current view automatically.

Audit results respect warehouse access and source record rules. The audit
updates only configuration and derived results; it never repairs stock,
identity, Product, Location, lifecycle, quality, or Final Lot links. Internal
Transfers do not automatically update Registry Location.

Devices
~~~~~~~

Open ``Pinout -> Device Registry -> Devices`` and create a record for each
physical device. Enter its Device UID and Device Type, then maintain Current
Product / Current Form, state, quality, and location as production progresses.

Quality Status is managed manually: Unknown is the default, Needs Test means a
check is required, Testing means a check is in progress, and OK / Failed record
its result. An interrupted check can return to Needs Test; a repeat check can
start from OK or Failed. Changes are tracked in chatter. Keep test notes and
report attachments in Log note, and use Physical Location Note for the device's
physical place. Changing quality does not change the lifecycle state, Product
Form, Final Lot, or inventory. Testing is available in Batch Update and as a
Device list filter; Bundle readiness still requires OK.

When the device becomes a finished retail unit, assign its Final Lot / Serial.
The serial number must exactly match Device UID and belong to the selected
Current Product. Completed deliveries, returns, and scrap operations update
unambiguous Device states automatically.

Current Final Lot / Serial represents only the active serial of the current
retail form. Clearing it keeps the Odoo lot, its Product, stock traceability,
and sales metadata in the Device's Final Lot History.

Use **Batch Create** in the Device list to register a new batch. Set Device Type
and the common fields once, then add editable rows or paste tab-separated
``UID / MAC / Address`` columns. Address means Robonomics Device Address. A
single UID column also works; MAC and Address are optional. Keep tabs for empty
cells. An optional first-line header is ``UID``, ``Device UID``,
``UID<TAB>MAC<TAB>Address`` or
``Device UID<TAB>MAC<TAB>Robonomics Device Address`` (case-insensitive).
Empty lines are ignored and surrounding cell whitespace is removed. **Parse /
Replace Rows** replaces the editable table; review its count, edit or remove
rows, and use **Check Rows** before **Create Devices**. If you edit the pasted
text again, parse it again; creation uses the table.

Only Device Type and Device UID must be supplied; State defaults to WIP and
Quality Status to Unknown. Product, location, physical note, MAC and Address
may remain empty. Checks report row numbers for missing, repeated or already
used UIDs, including archived Devices, without exposing existing record details.
Creation rechecks the rows and creates the whole batch atomically with your
normal access rights. A failure leaves the input available for correction;
success opens only the newly created Devices. Cancel creates no Devices. The
wizard creates registry identities without stock quantities, lots, bundles or
manufacturing orders.

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
