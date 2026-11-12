// Remembers where the library was (filters, page) so "Back" from a video can
// return there, whatever was opened in between (see the Back setting).
// router.js feeds it; this module imports nothing to avoid an import cycle.
let lastLibrary = { name: "library" };
export function rememberLibrary(route) { lastLibrary = { name: "library", query: { ...route.query } }; }
export const lastLibraryLocation = () => lastLibrary;
