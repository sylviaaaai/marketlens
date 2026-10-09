# Day 1–2: Data feasibility checklist

## Principle
Publicly visible content is not automatically available for automated bulk collection. Check platform terms, obtain permission where necessary, and do not bypass login challenges or access controls.

## Priority options
1. Authorized merchant export of aggregated product/content performance data.
2. Official API, if account and app permissions allow the needed fields.
3. Other explicitly licensed data source for a cross-category demonstration.

## Source audit questions
- Source name, owner, URL and permitted method of access?
- Is there documented authorization? Commercial/republication restrictions?
- Can we get product IDs, prices, a collection timestamp, displayed sales?
- Are sales counts exact, rounded, lower bounds, or otherwise unclear?
- Can we associate public posts and product SKUs reliably?
- Could the data contain buyer personal information? Exclude it.
- Can we repeat observations without breaking terms? How frequently?

## MVP go/no-go checkpoint after Day 2
GO if we have at least one permitted, attributable data source containing approximately 20 or more real product listings with usable product identifiers, names and price data. Sale-count fields optional: do not block basic price analytics.

If not: choose another permitted API or seller-authorized source; the data adapter and UI can remain unchanged.

## First research hypotheses (not conclusions)
- Are category-specific prices associated with different *displayed* sales levels?
- Do changes in price precede changes in observed displayed-sales counts?
- Where approved post data exists, does post engagement provide information beyond price/category?

Note: display counts and engagement are not direct measures of true conversions or post exposure.
