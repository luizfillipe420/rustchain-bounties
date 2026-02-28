import unittest

from scripts.sybil_risk_scorer import ClaimInput, extract_links, score_claims


class SybilRiskScorerTests(unittest.TestCase):
    def test_extract_links_canonicalizes_and_deduplicates(self):
        text = "Proof https://Example.com/path/?a=1 and https://example.com/path?a=1.)"
        self.assertEqual(extract_links(text), ("https://example.com/path?a=1",))

    def test_missing_fields_degrade_gracefully(self):
        claims = [
            ClaimInput(
                claim_id="c-1",
                user="missing-data",
                issue_ref="Scottcjn/rustchain-bounties#1",
                created_at="2026-02-28T00:00:00Z",
                body="Claiming this bounty.",
            )
        ]
        result = score_claims(claims)[0]
        self.assertEqual(result.score, 0)
        self.assertEqual(result.level, "low")

    def test_new_account_flags_account_age(self):
        claims = [
            ClaimInput(
                claim_id="c-1",
                user="new-user",
                issue_ref="Scottcjn/rustchain-bounties#1",
                created_at="2026-02-28T00:00:00Z",
                body="Claiming this bounty.",
                account_age_days=2,
            )
        ]
        result = score_claims(claims)[0]
        self.assertIn("ACCOUNT_AGE", result.reasons)
        self.assertGreater(result.score, 0)

    def test_wallet_reuse_flags_multiple_accounts(self):
        claims = [
            ClaimInput("c-1", "user-a", "Scottcjn/rustchain-bounties#1", "2026-02-28T00:00:00Z", wallet="shared_wallet"),
            ClaimInput("c-2", "user-b", "Scottcjn/rustchain-bounties#2", "2026-02-28T00:05:00Z", wallet="shared_wallet"),
        ]
        results = score_claims(claims)
        self.assertTrue(all("WALLET_REUSE" in result.reasons for result in results))

    def test_duplicate_proof_link_flags_multiple_accounts(self):
        claims = [
            ClaimInput(
                "c-1",
                "user-a",
                "Scottcjn/rustchain-bounties#1",
                "2026-02-28T00:00:00Z",
                proof_links=("https://example.com/proof",),
            ),
            ClaimInput(
                "c-2",
                "user-b",
                "Scottcjn/rustchain-bounties#2",
                "2026-02-28T00:05:00Z",
                proof_links=("https://example.com/proof",),
            ),
        ]
        results = score_claims(claims)
        self.assertTrue(all("PROOF_DUPLICATE" in result.reasons for result in results))

    def test_text_similarity_flags_template_reuse(self):
        body_a = "Claiming this bounty with a deterministic Python plan and draft PR within 24 hours."
        body_b = "Claiming this bounty with a deterministic Python plan and draft PR within 48 hours."
        claims = [
            ClaimInput("c-1", "user-a", "Scottcjn/rustchain-bounties#1", "2026-02-28T00:00:00Z", body=body_a),
            ClaimInput("c-2", "user-b", "Scottcjn/rustchain-bounties#2", "2026-02-28T00:05:00Z", body=body_b),
        ]
        results = score_claims(claims)
        self.assertTrue(any("TEXT_SIMILARITY" in result.reasons for result in results))

    def test_same_user_template_reuse_across_issues_is_flagged(self):
        body_a = "Claiming this bounty with a deterministic Python plan and draft PR within 24 hours."
        body_b = "Claiming this bounty with a deterministic Python plan and draft PR within 36 hours."
        claims = [
            ClaimInput("c-1", "same-user", "Scottcjn/rustchain-bounties#1", "2026-02-28T00:00:00Z", body=body_a),
            ClaimInput("c-2", "same-user", "Scottcjn/rustchain-bounties#2", "2026-02-28T00:05:00Z", body=body_b),
        ]
        results = score_claims(claims)
        self.assertTrue(all("SELF_TEMPLATE_REUSE" in result.reasons for result in results))

    def test_claim_velocity_and_repo_spread_flag_burst_claiming(self):
        claims = [
            ClaimInput("c-1", "bursty", "Scottcjn/rustchain-bounties#1", "2026-02-28T00:00:00Z"),
            ClaimInput("c-2", "bursty", "Scottcjn/Rustchain#2", "2026-02-28T00:05:00Z"),
            ClaimInput("c-3", "bursty", "Scottcjn/bottube#3", "2026-02-28T00:10:00Z"),
            ClaimInput("c-4", "bursty", "Scottcjn/rustchain-bounties#4", "2026-02-28T00:15:00Z"),
        ]
        result = score_claims(claims)[0]
        self.assertIn("CLAIM_VELOCITY", result.reasons)
        self.assertIn("REPO_SPREAD", result.reasons)

    def test_results_sort_descending_by_score(self):
        claims = [
            ClaimInput("c-1", "safe", "Scottcjn/rustchain-bounties#1", "2026-02-28T00:00:00Z", account_age_days=500),
            ClaimInput(
                "c-2",
                "risky",
                "Scottcjn/rustchain-bounties#2",
                "2026-02-28T00:01:00Z",
                account_age_days=1,
                wallet="shared_wallet",
            ),
            ClaimInput(
                "c-3",
                "other",
                "Scottcjn/rustchain-bounties#3",
                "2026-02-28T00:02:00Z",
                account_age_days=1,
                wallet="shared_wallet",
            ),
        ]
        results = score_claims(claims)
        self.assertEqual(results[0].user, "other")
        self.assertGreaterEqual(results[0].score, results[-1].score)


if __name__ == "__main__":
    unittest.main()
