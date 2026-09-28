# LearnMesh 3-Minute Live Demo Walkthrough

## The Non-Negotiable Thesis Proven Live:
> *"I corrected Agent A once, and Agent B learned from that correction later."*

---

## 3-Minute Step-by-Step Script

### Minute 0:00 - 0:30 | The Enterprise Fleet Problem
1. Open the LearnMesh Dashboard (`http://localhost:8000/app`).
2. Point out the Fleet Agents: **Billing Agent**, **Support Agent**, and **Account Management Agent**.
3. State the core problem:
   *"Today, AI agents operate in silos. If a human corrects a billing agent on a refund rule, that knowledge stays trapped in that single chat. A support agent repeats the exact same mistake with another customer."*

### Minute 0:30 - 1:00 | Step 1: Agent A Makes a Generic Mistake
1. Click **Agent Workspace**.
2. Select **Billing Agent**.
3. Customer Tier: **Enterprise**.
4. Request: `"Please issue an immediate refund of $14,500 on our enterprise contract."`
5. Click **Run Agent Pipeline**.
6. **Observation:** Billing Agent recommends automatic refund:
   *"I will process the refund for your account immediately as requested."* (No shared memories applied).

### Minute 1:00 - 1:40 | Step 2: Human Correction & Teach the Fleet
1. In the **Did the Agent Make a Mistake?** box, enter:
   `"Do not promise an immediate refund for enterprise-contract customers. VP approval is required first."`
2. Click **Capture Correction & Teach Fleet**.
3. The screen opens **Teach the Fleet**.
4. Show the Human Verification Gate:
   - Original action vs. Human correction.
   - Extracted lesson: *"Enterprise refund requests require approval prior to customer commitment."*
   - Fleet Scope: Notice **Billing**, **Support**, and **Account** are eligible, while **Warehouse Operations** is excluded.
5. Click **Promote & Teach the Fleet**.
6. **Observation:** Lesson is retained into **Hindsight** and assigned a real memory ID (e.g. `HM-0021`).

### Minute 1:40 - 2:20 | Step 3: Agent B Avoids the Mistake!
1. Switch to **Support Agent** in the Workspace.
2. Enter the same scenario:
   `"Customer Acme Corp requests an immediate refund of $14,500 on their enterprise subscription."`
3. Click **Run Agent Pipeline**.
4. **Observation:**
   - Support Agent now responds:
     *"Because this involves an enterprise contract, approval from account leadership must be verified before committing to a refund. I have initiated the approval workflow."*
   - Notice the citation banner: **Recalled & Applied Shared Memory: HM-0021**!
   - Learned from: **Billing Agent**.

### Minute 2:20 - 2:45 | Step 4: Outcome Confirmation & Reinforcement
1. Click **✓ Confirm Correct** under the Support Agent's response.
2. The system confirms the positive outcome and reinforces confidence in `HM-0021`.

### Minute 2:45 - 3:00 | Step 5: Provenance & Lineage
1. Click **Learning Lineage** to show the complete traceable lifecycle:
   $$\text{Human Correction} \to \text{Billing Agent} \to \text{Hindsight Retention} \to \text{Support Agent Recall} \to \text{Reinforced Confidence}$$
2. Final Statement:
   *"We didn't train a second agent. We taught the organization once."*
