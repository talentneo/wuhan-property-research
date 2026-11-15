# Privacy Notes

This project is designed for public-property research. It does not contain telemetry, analytics, accounts, API keys, cookies, or a backend.

## What may be stored on your computer

- `wuhan-output/`: generated project reports, room tables, prices, and query parameters.
- `~/.cache/wuhan-property-research/` by default: cached URL responses keyed by a hash of the requested URL.
- Logs are not written by the script, but your shell or operating system may retain command history.

These files can reveal which projects, districts, layouts, price ranges, and buying intentions you researched. Keep them private or delete them when they are no longer needed. Use `--no-cache` to prevent local URL-response caching.

## Do not submit personal contract data

The Wuhan personal commodity-housing contract filing service may require a filing registration number, ID number, district, and project name. Do not automate that service with this script and do not provide such details to an AI tool. Use the official page personally if you choose to do so.

Never store or share:

- full ID numbers
- contract or filing registration numbers
- passwords, verification codes, or session cookies
- buyer names, phone numbers, or bank details
- screenshots or copies of personal filing results

A shared report should say only that the buyer personally verified the record on the official page and include the verification date.

## Network caveat

Some official endpoints use plain HTTP. Treat project names, addresses, and all query values as potentially observable on the network.
