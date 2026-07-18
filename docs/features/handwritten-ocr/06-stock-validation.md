# 06 · Stock Validation

> Given a resolved inventory match, decides whether the match is usable (stock > 0) or whether it should be substituted with an equivalent in-stock item. This is the last stage before file 07 simulates the deduction across the batch.

**Depends on:** `05-fuzzy-matching.md`.
**Feeds:** `07-stock-simulation.md`, `08-flag-system.md`.

---

## Purpose

The inventory contains items that were once stocked but are now at zero. Handwritten forms are often written by field staff who don't know the current stock state; they write the name of the item they physically received. In many cases the item they actually got is a **rename / re-SKU** of the original — same product, new SAP.

This file catalogues those known-good substitutions and applies them deterministically. It **never** substitutes based on similarity alone; only entries in the substitution table are eligible.

---

## Contract

```
input: {
  match: InventoryItem,             # from file 05
  requested_qty: number,
}

output: {
  resolved_item: InventoryItem,     # possibly substituted
  substituted: bool,
  substitution_reason: string | null,
  original_sap: string | null,      # only set when substituted
  stock_at_resolution: number,
  blocked: bool,                    # true = cannot post, no substitute
  block_reason: string | null,
}
```

`blocked = true` means: the requested item is zero-stock AND no substitution rule applies. File 07 skips this row. File 08 marks it 🚨 red.

---

## The Rules (in order)

### R1 — Match has stock: pass through

If `match.current_stock > 0` (as of the pre-batch snapshot; file 07 handles running totals), return it unchanged with `substituted = false`.

### R2 — Match is zero: consult the substitution table

Look up `match.sap_code` in the substitution table (below). If a rule exists and its `substitute_with` SAP has stock:

- Set `resolved_item` to the substitute
- `substituted = true`
- Record `original_sap` and `substitution_reason`

### R3 — Match is zero, no substitution rule: block

- `blocked = true`
- `block_reason = "zero_stock_no_substitute"`
- File 08 flags 🚨

### R4 — Substitute is also zero

Fall through as if no substitution existed → block. Do not chase a chain of substitutions; the table always maps to a *known-plentiful* alternative.

---

## Substitution Table

Every entry has three parts: what's out, what to use, and why. **The list is closed.** New entries require explicit approval and a note.

```yaml
substitutions:

  - out_sap: 1107
    out_name: "Ear Plug"
    substitute_sap: 1097
    substitute_name: "Ear PLUG Vaultex"
    reason: "Vaultex is the current supplier; the generic SAP 1107 was retired in inventory but still appears on old forms."

  - out_sap: 1137
    out_name: "LEATHER GLOVES"
    substitute_sap: 1165
    substitute_name: "Leather gloves"
    reason: "Bulk-pack replacement (240 pcs/box) took over from the single-pair SAP."

  - out_sap: 1176
    out_name: "Tar Paulin 10x10 orange"
    substitute_sap: 1076
    substitute_name: "TARPAULIN Small 10X10"
    reason: "Colour distinction dropped; only one 10x10 tarp now stocked."

  - out_sap: 1235
    out_name: "CABLE TIE 430X7.6 BLACK"
    substitute_sap: 1163
    substitute_name: "Cable Tie Wire (Nylon)"
    reason: "Consolidated cable-tie SKU."

  - out_sap: 1271
    out_name: "SAFETY VEST (RED-SBM)"
    substitute_sap: 1272
    substitute_name: "SAFETY VEST (RED-HSE OFFICERS)"
    reason: "Same vest, HSE re-labelled."

  - out_sap: 1141
    out_name: "Fire Extinguisher"
    substitute_sap: 1072
    substitute_name: "Fire Extinguisher DCP"
    reason: "Generic FE SKU retired in favour of the DCP-specific one."

  - out_sap: 1105
    out_name: "Tie Wire"
    substitute_sap: 1163
    substitute_name: "Cable Tie Wire (Nylon)"
    reason: "Merged with cable-tie stock."

  - out_sap: 1237
    out_name: "CABLE TIE 300X4.8MM"
    substitute_sap: 1163
    substitute_name: "Cable Tie Wire (Nylon)"
    reason: "Merged with cable-tie stock."
```

Non-substitutable zero-stock items (the ones that block outright):

- `1229` UVEX MONO GOGGLE — no direct replacement; users are told to substitute at their discretion via the UI
- Any item where the operator's request is a *category*, not an item (e.g. "Safety goggles" — resolved item is already the closest match)

---

## Substitutes Are Not Suggested Silently

When `substituted = true`, the UI must show:

- The name the operator wrote
- The SAP that was actually posted against
- The `substitution_reason` string

The operator can then reject the substitution in the Issue page (which would cancel the row and require a manual entry).

---

## Interaction with Fuzzy Matching (file 05)

The substitution table applies **after** fuzzy matching. File 05 might resolve `Head Lamp 24V` to `HANGLING LAMP 24V` (SAP 1083). If SAP 1083 is zero, file 06 checks whether 1083 has a substitution rule. It does not; the row is blocked.

If the operator wrote `Ear Plug` and file 05 confidently resolves to SAP 1107, file 06 substitutes to 1097 via R2. This is the normal path.

---

## When the Substitution Table Grows

Add a new entry only when:

1. The item has been zero-stock for **≥ 30 days**.
2. There is one obvious, single-SKU replacement (not "one of these three").
3. A stakeholder (site manager or storekeeper) has confirmed the mapping.
4. The `reason` is written in the operator's language (not internal jargon).

Do not add speculative substitutions.

---

## Pseudo-code

```python
# The substitution table is a constant. Prefer loading from YAML in metadata.
SUBSTITUTIONS = {
    "1107": {"to": "1097", "reason": "Vaultex is current supplier"},
    "1137": {"to": "1165", "reason": "Bulk-pack replacement"},
    "1176": {"to": "1076", "reason": "Colour distinction dropped"},
    "1235": {"to": "1163", "reason": "Consolidated cable-tie SKU"},
    "1271": {"to": "1272", "reason": "HSE re-label"},
    "1141": {"to": "1072", "reason": "Generic FE retired for DCP-specific"},
    "1105": {"to": "1163", "reason": "Merged with cable-tie stock"},
    "1237": {"to": "1163", "reason": "Merged with cable-tie stock"},
}

def validate_stock(match: dict,
                   requested_qty: float,
                   inventory_by_sap: dict[str, dict]) -> dict:
    match_stock = float(match.get("current_stock") or 0)

    if match_stock > 0:
        return {
            "resolved_item": match,
            "substituted": False,
            "substitution_reason": None,
            "original_sap": None,
            "stock_at_resolution": match_stock,
            "blocked": False,
            "block_reason": None,
        }

    # Zero stock — try substitution
    rule = SUBSTITUTIONS.get(str(match["sap_code"]))
    if rule:
        sub = inventory_by_sap.get(rule["to"])
        if sub and float(sub.get("current_stock") or 0) > 0:
            return {
                "resolved_item": sub,
                "substituted": True,
                "substitution_reason": rule["reason"],
                "original_sap": str(match["sap_code"]),
                "stock_at_resolution": float(sub["current_stock"]),
                "blocked": False,
                "block_reason": None,
            }

    # Blocked
    return {
        "resolved_item": match,
        "substituted": False,
        "substitution_reason": None,
        "original_sap": None,
        "stock_at_resolution": 0,
        "blocked": True,
        "block_reason": "zero_stock_no_substitute",
    }
```

---

## Test Cases

| # | Input match | Stock | Expected outcome |
|---|---|---|---|
| 1 | SAP 1145 Green Coated gloves | 73 | Passthrough, `substituted=false` |
| 2 | SAP 1137 LEATHER GLOVES | 0 | Substituted to SAP 1165 |
| 3 | SAP 1107 Ear Plug | 0 | Substituted to SAP 1097 |
| 4 | SAP 1229 UVEX MONO GOGGLE | 0 | Blocked (`zero_stock_no_substitute`) |
| 5 | SAP 1141 Fire Extinguisher | 0 | Substituted to SAP 1072 |
| 6 | SAP 1137 LEATHER GLOVES | 0 (and SAP 1165 also 0) | Blocked (no chained substitution) |
| 7 | SAP 1131 Dust Mask | 1920 | Passthrough |

---

## Claude Code Metadata

```yaml
module: stock-validation
version: 1.0
depends_on: [fuzzy-matching]
feeds: [stock-simulation, flag-system]

rules_in_order:
  - R1_passthrough_if_stock_positive
  - R2_substitute_if_zero_and_rule_exists_and_substitute_has_stock
  - R3_block_if_zero_and_no_rule
  - R4_block_if_substitute_also_zero

substitutions:
  - {out: 1107, in: 1097, reason: "Vaultex is current supplier"}
  - {out: 1137, in: 1165, reason: "Bulk-pack replacement"}
  - {out: 1176, in: 1076, reason: "Colour distinction dropped"}
  - {out: 1235, in: 1163, reason: "Consolidated cable-tie SKU"}
  - {out: 1271, in: 1272, reason: "HSE re-label"}
  - {out: 1141, in: 1072, reason: "Generic FE retired for DCP-specific"}
  - {out: 1105, in: 1163, reason: "Merged with cable-tie stock"}
  - {out: 1237, in: 1163, reason: "Merged with cable-tie stock"}

closed_list: true    # substitutions can only be added via explicit spec change
chained_substitution: false
show_substitution_in_ui: mandatory
```
