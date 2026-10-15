// The backend's rule for a saved report's title: 1-120 characters after trimming, counted as
// characters (code points, as the backend counts them), and no control characters such as tabs.
// A title is checked, never shortened behind the user's back.

export const MAX_TITLE_LENGTH = 120

/** Why title cannot be saved, or null. */
export function titleProblem(title) {
  const trimmed = title.trim()
  if (!trimmed) return 'Enter a title for this report.'
  const length = [...trimmed].length
  if (length > MAX_TITLE_LENGTH) return `Titles can be up to ${MAX_TITLE_LENGTH} characters. This one has ${length}.`
  if (/\p{Cc}/u.test(trimmed)) return 'Titles cannot contain tabs or line breaks.'
  return null
}

/** True when title is over the limit (shown at once, since a long question can be prefilled). */
export function isTitleTooLong(title) {
  return [...title.trim()].length > MAX_TITLE_LENGTH
}
