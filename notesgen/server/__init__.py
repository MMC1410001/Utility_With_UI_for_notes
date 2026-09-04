"""The local web UI for notesgen.

Runs on the user's own machine (`python3 -m notesgen serve`), which is what
makes the whole thing tractable: the existing `.env`, the claude CLI, the
logged-in Chrome profile and the desktop Google OAuth token all keep working
exactly as they do for the command line.
"""
