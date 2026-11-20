// Who made a video (3.6.0): `creators` is the site's "creator" list (can differ from the
// uploader and name several people); `creator` is the account that uploaded it. A video is
// listed under both; when no creator is known, the uploader counts as the creator.
export const makers = (it) => (it?.creators?.length ? it.creators : it?.creator ? [it.creator] : []);
export const creatorText = (it) => makers(it).join(", ");
/** The uploader when it isn't one of the creators (shown as "uploaded by …"), else "". */
export const uploadedBy = (it) =>
  it?.creator && !makers(it).some((m) => m.toLowerCase() === it.creator.toLowerCase()) ? it.creator : "";
/** Everyone it's listed under: creators, then the uploader. */
export const madeOrUploadedBy = (it) => [...new Set([...(it?.creators || []), ...(it?.creator ? [it.creator] : [])])];
