# Marks this directory as a package. Each sibling module here holds one
# cohesive piece of what used to be a single ~1,500-line library_manager.py:
# either a mixin class (one worker/concern's methods) or, for _helpers.py,
# plain module-level utility functions with no shared state. See
# library_manager.py's own top-of-file comment for how these get composed
# back into the single LibraryManager class + singleton once every mixin
# exists (the final "cutover" step) -- until then, library_manager.py
# keeps its own working copies of everything and these files are purely
# additive, not yet imported by anything.
