# Marks this directory as a package. Each sibling module here holds one
# domain's routes, split out of what used to be a single ~700-line
# main.py -- see main.py's own top-of-file comment for the include_router()
# wiring that assembles them all back into one FastAPI app. This is a
# modular monolith split (organization for maintainability), NOT a
# microservices split -- every router here still runs in the same single
# process, shares the same LibraryDB connection, and has no independent
# deployment lifecycle. See _common.py for small helpers shared by more
# than one router (kept here rather than in main.py, so routers don't
# have to import from main.py and risk a circular import).
