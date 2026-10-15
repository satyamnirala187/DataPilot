/**
 * "Your question", the question as the page heading, and optionally one headline sentence.
 * The headline is worked out from the result's own rows (utils/result.js), never written by AI;
 * when no sentence can be stated with certainty there is none.
 */
export default function QuestionHeader({ question, headline = null }) {
  return (
    <div className="dp-question-header">
      <p className="dp-question-label">Your question</p>
      <h1 className="dp-question-title">{question}</h1>
      {headline && <p className="dp-result-headline">{headline}</p>}
    </div>
  )
}
