# Working on desk-buddy

This folder is a public GitHub repo (desk-buddy). Rules for every change:

1. **Commit and push after every finished change** -- one clear commit
   message per change, then `git push`. Nothing half-done.
2. **Never commit personal data.** Personal files are git-ignored and must
   stay that way: config/jarvis_sources.json, config/.env.local_model,
   config/mode, database/, logs/, models/, DEVELOPMENT_LOG.md,
   PERSONAL_README.md, TASKS.md. Before committing, check `git status` and
   the diff for names, home paths (/Users/...), emails, API keys, or
   anything from the owner's own files. Code must read the user's name /
   city / projects from config, never hard-code them.
3. Keep both install modes working: `./install.sh --pet-only` (computer/pet
   only, Node) and `./install.sh` (JARVIS + pet).
4. Log what changed in DEVELOPMENT_LOG.md (private) and, when it matters to
   users, in README.md.
