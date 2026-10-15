// The page-navigation loader: PageBar is a thin moving line just under the top bar (placed first in
// the main area), PageSkeleton the pulsing blocks in the page's shape. Neither shows a progress
// figure, since a request reports none.

export function PageBar() {
  return (
    <div className="dp-page-bar" aria-hidden="true">
      <span />
    </div>
  )
}

/**
 * label is the one status announced to screen readers ("Loading history…").
 * variant: 'list' (History rows) or 'result' (a stored analysis).
 */
export function PageSkeleton({ label, variant = 'list' }) {
  return (
    <>
      <p role="status" className="visually-hidden">
        {label}
      </p>
      <div className="dp-skeleton" aria-hidden="true">
        <span className="dp-skeleton-block dp-skeleton-title" />
        <span className="dp-skeleton-block dp-skeleton-line" />
        {variant === 'result' ? (
          <div className="dp-skeleton-result">
            <div className="dp-skeleton-tiles">
              {[0, 1, 2, 3].map((index) => (
                <span key={index} className="dp-skeleton-block dp-skeleton-tile" />
              ))}
            </div>
            <span className="dp-skeleton-block dp-skeleton-hero" />
          </div>
        ) : (
          <div className="dp-card dp-skeleton-rows">
            {[0, 1, 2].map((index) => (
              <div key={index} className="dp-skeleton-row">
                <span className="dp-skeleton-block dp-skeleton-chip" />
                <span className="dp-skeleton-text">
                  <span className="dp-skeleton-block dp-skeleton-question" />
                  <span className="dp-skeleton-block dp-skeleton-meta" />
                </span>
                <span className="dp-skeleton-block dp-skeleton-time" />
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}
