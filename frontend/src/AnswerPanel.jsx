export default function AnswerPanel({ answer }) {
  return (
    <section className="panel" aria-labelledby="answer-heading">
      <h2 id="answer-heading">Answer</h2>
      <p className="answer">{typeof answer === "string" && answer.trim() ? answer : "No answer was returned."}</p>
    </section>
  );
}
