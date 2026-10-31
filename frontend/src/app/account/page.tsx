import Link from "next/link";

export default function Account() {
  return <main style={{ maxWidth: 560, margin: "4rem auto", padding: 24 }}>
    <h1>Account privacy</h1>
    <p>Account deletion is currently unavailable while backup erasure and privacy review are completed.</p>
    <p>Deletion will permanently remove your account, chats and bookmarks. It cannot be undone.
      Backups may retain historical records until their retention period ends; approved erasure requests
      must be reapplied before any restored backup can serve users.</p>
    <p>When enabled, deletion will require your current password, an explicit confirmation and a final review.
      Signing out does not delete your data.</p>
    <button disabled aria-disabled="true">Delete account (unavailable)</button>
    <p><Link href="/account-recovery">Password and email recovery</Link></p><Link href="/">Back to NAFSIY</Link>
  </main>;
}
