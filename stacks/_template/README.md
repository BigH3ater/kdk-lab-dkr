# Stack template

1. Copy: `cp -r stacks/_template stacks/<name>` and replace every `template` (name, container names, routers, paths, op refs).
2. Trim what you do not need (db, Traefik labels, `.env.tpl`) and rewrite the header comment.
3. Register it as a `[[stack]]` in `komodo/resources.toml` (unregistered dirs, like this one, are never deployed).
4. Run `python3 scripts/compose-baseline-check.py --stack <name>`; fix gaps or add a documented skip/exception label.
5. Standard and recipes: [docs/reference/compose-baseline.md](../../docs/reference/compose-baseline.md).
