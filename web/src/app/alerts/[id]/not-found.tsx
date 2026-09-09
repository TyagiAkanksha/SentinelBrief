import Link from "next/link";

export default function AlertNotFound() {
  return (
    <section>
      <h1>Alert not found</h1>
      <p>No alert has that id.</p>
      <Link href="/alerts">Back to the queue</Link>
    </section>
  );
}
