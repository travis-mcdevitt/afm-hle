# Image encoding correction

Protocol `afm-hle-v3` verifies actual image bytes rather than trusting the inline
MIME label. Correct PNG and JPEG encodings retain their bytes. Static WebP and
GIF are converted to RGBA PNG without resizing or changing decoded pixels.
Animated images remain unsupported. Dataset contents, question order, sample,
and prompt remain unchanged.

On October 1, 2026, a Pro request was rejected before inference with
`invalid_image`: an inline JPEG label contained WebP bytes. The correction is
an explicit protocol migration, not a silent change to a frozen run.

`PYTHONPATH=. .venv/bin/python scripts/migrate-image-v3.py DIRECTORY` migrates
stopped v2 campaigns. It checks all historical attempts and refuses migration
if any changed payload was successfully dispatched or has an ambiguous outcome.
Only blocked `invalid_image` attempts may have a changed future payload.
It backs up both generation and judging databases, archives the original
generation and judge manifests in `protocol_history`, and updates the judge's
generation provenance hash without changing the grader configuration or grades.
Private migration records contain hashes of the original and corrected payload.
The script preserves every attempt, grade, cost, and sampling-plan hash.

The two Mac campaigns were migrated together. The blocked Pro item was then
explicitly resolved for retry; its original rejected attempt remains intact.
The image test suite checks pixel preservation, unchanged valid JPEG bytes,
mislabeled WebP, and rejection of animation.
