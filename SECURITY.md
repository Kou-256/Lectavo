# Security policy

Please do not open a public issue for a vulnerability that could expose local
files, credentials, or generated lecture content. Use GitHub's private
vulnerability reporting feature for `Kou-256/Lectavo` when available.

Lectavo processes local media and downloads the selected faster-whisper model
from Hugging Face on first use. It does not require an API key and does not
upload input videos, generated slides, transcripts, or lecture notes to a
Lectavo server.

Only releases published from the official GitHub repository should be treated
as official builds. Compare downloaded files with the SHA-256 value attached to
the release. The current `0.2.1` build is unsigned; Windows SmartScreen may show
a warning until Authenticode signing is configured.
