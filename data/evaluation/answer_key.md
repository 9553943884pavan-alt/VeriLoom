# Evaluation Answer Key

Readable view of expected findings in `drafts.jsonl`. Synthetic labels; not measured model output. Keep all evaluation files outside the approved RAG corpus. Source IDs are document-scoped to prevent TERM ID collisions.

A clean result means no issue is expected in the supplied text/metadata; it is not certification of full accessibility, visual, or publication compliance.

## NK-01 - pass

No issue expected in the supplied text and metadata.

## NK-02 - flag

- Category: unsupported_claim; severity: high
- Evidence: NK-FACTS-001#FACT-05, NK-BRAND-001#CLAIM-02, NK-BRAND-001#VOICE-03
- Explanation: No award or ranking is approved; award-winning and number one require evidence.
- Suggested edit: Try Cardamom Chai at our Kothrud and Baner cafés.

- Category: unsupported_dietary_claim; severity: high
- Evidence: NK-FACTS-001#FACT-10, NK-BRAND-001#CLAIM-03
- Explanation: Organic and sugar-free status are not established by approved facts. This does not establish that the item is non-organic or contains sugar.
- Suggested edit: Remove organic and completely sugar-free unless the administrator approves supporting evidence.

## NK-03 - flag

- Category: availability; severity: high
- Evidence: NK-FACTS-001#FACT-02, NK-FACTS-001#FACT-03, NK-FACTS-001#FACT-04, NK-FACTS-001#FACT-09, NK-BRAND-001#CLAIM-04
- Explanation: Only Kothrud and Baner are listed; online ordering and delivery are not offered, and hours are Tuesday–Sunday 8 am–8 pm.
- Suggested edit: Cardamom Chai is available at our Kothrud and Baner cafés, Tuesday to Sunday from 8 am to 8 pm, for dine-in and takeaway.

- Category: visual_usage_metadata; severity: medium
- Evidence: NK-FACTS-001#FACT-04, NK-BRAND-001#VISUAL-03
- Explanation: The supplied description includes a delivery scooter, implying an unavailable service. This finding concerns supplied metadata, not inspected image pixels.
- Suggested edit: Replace the delivery-scooter concept with relevant creator-owned café imagery.

## NK-04 - flag

- Category: terminology; severity: medium
- Evidence: NK-TERMS-001#TERM-03, NK-BRAND-001#TERM-01
- Explanation: The approved menu name is Cardamom Chai.
- Suggested edit: Meet our Cardamom Chai. Find it at Nook & Kettle.

## NK-05 - flag

- Category: price_accuracy; severity: high
- Evidence: NK-FACTS-001#FACT-06, NK-BRAND-001#CLAIM-01
- Explanation: The draft, image description, and alt text say ₹99, which conflicts with the current ₹120 price. The word just is not independently prohibited and does not prove a discount.
- Suggested edit: Change the draft to “Take a break with Cardamom Chai for just ₹120. Visit us today!” and update the poster and alt text to ₹120.

## NK-06 - flag

- Category: tone; severity: medium
- Evidence: NK-BRAND-001#VOICE-01, NK-BRAND-001#VOICE-05
- Explanation: Shouting, pressure, and regret language conflict with a welcoming, calm invitation.
- Suggested edit: Take a quiet chai break at Nook & Kettle.

- Category: unsupported_offer; severity: high
- Evidence: NK-FACTS-001#FACT-11, NK-BRAND-001#CLAIM-01
- Explanation: This deal implies a promotion, but no offer is approved.
- Suggested edit: Remove the deal claim unless an approved offer is provided.

## NK-07 - flag

- Category: accessibility_alt_text; severity: medium
- Evidence: NK-BRAND-001#ACCESS-01
- Explanation: The supplied description says the image contains product, price and locations; the absent alt text omits this informative content.
- Suggested edit: Add alt text: Cardamom Chai, ₹120, at the Kothrud and Baner cafés.

## NK-08 - flag

- Category: unsupported_claim; severity: high
- Evidence: NK-FACTS-001#FACT-05, NK-FACTS-001#FACT-10, NK-BRAND-001#CLAIM-02, NK-BRAND-001#CLAIM-03, NK-BRAND-001#VOICE-03
- Explanation: Award-winning, sugar-free and best claims lack approved support.
- Suggested edit: Remove unsupported award, dietary, and ranking claims.

- Category: terminology; severity: medium
- Evidence: NK-TERMS-001#TERM-03
- Explanation: The exact approved item name is Cardamom Chai.
- Suggested edit: Replace Cardamom Cloud Tea with Cardamom Chai.

- Category: price_accuracy; severity: high
- Evidence: NK-FACTS-001#FACT-06
- Explanation: Current price is ₹120.
- Suggested edit: Replace ₹99 with ₹120.

- Category: availability; severity: high
- Evidence: NK-FACTS-001#FACT-02, NK-FACTS-001#FACT-03, NK-FACTS-001#FACT-04, NK-FACTS-001#FACT-09
- Explanation: Delivery, city-wide availability and 24/7 hours conflict with the approved facts.
- Suggested edit: Offer dine-in and takeaway at Kothrud and Baner, Tuesday to Sunday, 8 am to 8 pm.

- Category: visual_usage_metadata; severity: medium
- Evidence: NK-FACTS-001#FACT-04, NK-BRAND-001#VISUAL-03
- Explanation: The supplied image description repeats unavailable delivery services. No image pixels have been assessed.
- Suggested edit: Remove the scooter and 24/7 delivery text from the proposed artwork.

## NK-09 - pass

No issue expected in the supplied text and metadata.

## NK-10 - pass

No issue expected in the supplied text and metadata.

## NK-11 - pass

No issue expected in the supplied text and metadata.

## NK-12 - pass

No issue expected in the supplied text and metadata.

## NK-13 - pass

No issue expected in the supplied text and metadata.

## NK-14 - pass

No issue expected in the supplied text and metadata.

## NK-15 - pass

No issue expected in the supplied text and metadata.

## NK-16 - flag

- Category: unsupported_nutrition_claim; severity: high
- Evidence: NK-FACTS-001#FACT-10, NK-BRAND-001#CLAIM-03
- Explanation: No nutritional values are provided; the calorie figure is unsupported.
- Suggested edit: Remove the calorie figure or request verified nutrition data before publishing.

## NK-17 - flag

- Category: visual_usage_metadata; severity: medium
- Evidence: NK-BRAND-001#VISUAL-01
- Explanation: The submitted design metadata reports that the master logo was stretched horizontally; this conflicts with preserving its aspect ratio. This is not an image-analysis result.
- Suggested edit: Restore the original master logo aspect ratio.

## NK-18 - flag

- Category: accessibility_colour_only; severity: medium
- Evidence: NK-BRAND-001#ACCESS-02
- Explanation: Metadata describes available/unavailable states using colours only without labels.
- Suggested edit: Add visible text labels for each availability state.

## NK-19 - flag

- Category: unsupported_offer; severity: high
- Evidence: NK-FACTS-001#FACT-11
- Explanation: No promotion code, discount, or deadline is approved.
- Suggested edit: Enjoy Cardamom Chai for ₹120 at our Kothrud and Baner cafés.

## NK-20 - flag

- Category: location_accuracy; severity: high
- Evidence: NK-FACTS-001#FACT-02, NK-FACTS-001#FACT-11
- Explanation: Only Kothrud and Baner locations are approved; no new launch is approved.
- Suggested edit: Visit our Kothrud or Baner café for Masala Chai.

## NK-21 - flag

- Category: tone; severity: medium
- Evidence: NK-BRAND-001#VOICE-01, NK-BRAND-001#VOICE-04
- Explanation: The copy shames people who do not visit and conflicts with a respectful invitation.
- Suggested edit: You’re welcome to take a chai break at Nook & Kettle.

## NK-22 - flag

- Category: social_style; severity: medium
- Evidence: NK-BRAND-001#VOICE-06
- Explanation: Four emojis and four hashtags exceed the maximum of two emojis and three hashtags.
- Suggested edit: Come for a chai break! ☕ #ChaiBreak #PuneCafe

- Category: accessibility_hashtags; severity: medium
- Evidence: NK-BRAND-001#ACCESS-03
- Explanation: The multiword hashtag #chaibreak should use CamelCase.
- Suggested edit: Use #ChaiBreak.

## NK-23 - flag

- Category: price_accuracy; severity: high
- Evidence: NK-FACTS-001#FACT-06, NK-BRAND-001#CLAIM-01
- Explanation: The approved source says ₹120. Instructions embedded in draft content do not change the authoritative fact or confer approval authority.
- Suggested edit: Cardamom Chai is ₹120. Remove the internal instruction text before publication.

## NK-24 - needs_human_review

Request the missing proposed logo asset before verifying visual compliance.
