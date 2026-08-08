markdown # 🧠 Cortex

> *"An agent that remembers not just the solution, but the shortest path to finding it."*

[![Python](https://img.shields.io/badge/Python-3.10+-**3776AB**?style=for-the-badge&logo=python&logoColor=white)](https://[www.python.org/](https://www.python.org/)) [![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-**1C3C3C**?style=for-the-badge&logo=langchain&logoColor=white)](https://langchain-ai.github.io/langgraph/) [![DataHub](https://img.shields.io/badge/DataHub-0.14+-**6633CC**?style=for-the-badge&logo=datahub&logoColor=white)](https://datahubproject.io/) [![ChromaDB](https://img.shields.io/badge/ChromaDB-0.5+-yellow?style=for-the-badge)](https://[www.trychroma.com/](https://www.trychroma.com/)) [![Groq](https://img.shields.io/badge/Groq-**LLM**-**FF6B00**?style=for-the-badge)](https://groq.com/) [![License](https://img.shields.io/badge/License-Apache%**202**.0-blue.svg?style=for-the-badge)](**LICENSE**) [![Hackathon](https://img.shields.io/badge/Build%20with-DataHub-**6633CC**?style=for-the-badge)](https://datahub.devpost.com/)

---

## 📖 Table of Contents

- [Overview](#-overview)
- [The Problem](#-the-problem)
- [What Makes Cortex Different](#-what-makes-cortex-different)
- [Memory Behaviors](#-memory-behaviors)
- [Mathematical Formalism](#-mathematical-formalism)
- [Architecture](#-architecture)
- [Quick Start](#-quick-start)
- [Configuration](#-configuration)
- [Running Cortex](#-running-cortex)
- [Testing](#-testing)
- [Project Structure](#-project-structure)
- [Built With](#-built-with)
- [License](#-license)

---

## 🎯 Overview

**Cortex is an AI Data Reliability Engineer with Self-Verifying Memory.**

It investigates data warehouse incidents, checks episodic memory for prior occurrences, verifies if current metadata in DataHub matches previously successful fixes, traverses lineage graphs when needed, proposes automated dbt/**SQL** fixes for human approval, and promotes validated lessons back into DataHub as semantic aspects.

**The core insight:** Similar incidents don't always mean the same solution still applies. The data environment may have changed. Cortex fact-checks its own memory against reality before reusing any solution.

---

## 🔥 The Problem

When a data pipeline breaks, an engineer must:

## Identify the failing dataset.

## Trace its lineage. 
## Compare schemas. 
## Check upstream dependencies. 
## Search previous incidents. 
## Determine the root cause. ## Figure out whether someone already solved this.

**Organizations accumulate this knowledge—but it's scattered across catalogs, documentation, logs, and the engineers who investigated them.**

Data incidents are rarely difficult because engineers don't know how to fix them. They're difficult because the investigation has to be repeated.

---

## 💡 What Makes Cortex Different

| Traditional AI Agents | Cortex |
| :--- | :--- |
| Retrieve the most similar answer | Retrieve **AND verify** against live state |
| Trust their memory unconditionally | **Fact-check** their memory against DataHub |
| Start fresh every session | **Remember** and apply past experiences |
| Consume metadata | **Write knowledge back** to DataHub |
| Answer questions | **Investigate and propose fixes** |
| Black-box reasoning | **Transparent decision trace** |

---

## 🧠 Memory Behaviors

Cortex implements **five distinct memory behaviors**, each optimized for a specific scenario:

### 1. Cold Start

**No trusted precedent exists in ChromaDB** → Cortex traverses the DataHub lineage graph from scratch. Incident → No Memory → Full Lineage Traversal → Root Cause → Fix

text

### 2. Same Asset, Changed State

**Precedent exists but the environment has changed** → Cortex uses the previous investigation as a seed, then validates newly modified nodes. Incident → Memory Found → State Diff > 0 → Seeded Investigation → Root Cause → Fix

text

### 3. Same Asset, Unchanged State (Contradiction)

**Precedent exists, environment unchanged, but the same incident recurred** → This is a **contradiction**. A previously approved fix failed to permanently solve the problem. Cortex re-traverses lineage while carrying previous evidence forward to locate hidden root causes. Incident → Memory Found → State Diff = 0 → ⚠️ Contradiction → Re-investigate → Deeper Root Cause → Fix

text

### 4. Different Asset, High-Confidence Pattern Match

**Different asset, similar failure pattern** → Cortex reuses the learned fix pattern without assuming structural schema identity. Incident (Asset B) → Memory (Asset A) → Pattern Match → Reuse Learned Fix → Verify → Fix

text

### 5. Rejection Safety Guard

**Any fix marked as Rejected by a human engineer** → Its eligibility weight is set to zero, preventing re-prompts for failed fixes. Incident → Memory Found → Previous Fix Rejected → Investigate Fresh → New Fix

text

---

## 📐 Mathematical Formalism

### State Snapshot Formalism

An asset snapshot $S$ at time $t$ for asset $A$ is represented as a tuple:

$$S(A_t) = \langle \mathcal{S}(A_t), \tau(A_t), \mathcal{L}(A_t) \rangle$$

Where:
- $\mathcal{S}(A_t)$ is the schema definition (column names, data types, nullability constraints)
- $\tau(A_t)$ is the metadata freshness timestamp or modification marker
- $\mathcal{L}(A_t) = \{U_1, U_2, \dots, U_k\}$ is the set of upstream lineage dataset dependencies

### State Difference Function

$$\Delta(S_1, S_2) = w_1 \cdot \mathbb{I}(\mathcal{S}_1 \neq \mathcal{S}_2) + w_2 \cdot \mathbb{I}(\tau_1 \neq \tau_2) + w_3 \cdot D_{\text{Jaccard}}(\mathcal{L}_1, \mathcal{L}_2)$$

Where $\mathbb{I}(\cdot)$ is the indicator function and $D_{\text{Jaccard}}$ is the Jaccard distance:

$$D_{\text{Jaccard}}(\mathcal{L}_1, \mathcal{L}_2) = 1 - \frac{|\mathcal{L}_1 \cap \mathcal{L}_2|}{|\mathcal{L}_1 \cup \mathcal{L}_2|}$$

**Decision Outcomes:**
- **Identical State:** $\Delta(S_1, S_2) = 0$
- **Diverged State:** $\Delta(S_1, S_2) > 0$

### Episodic Memory Similarity

$$\text{Sim}(e_q, e_i) = \frac{\mathbf{v}_q \cdot \mathbf{v}_i}{\|\mathbf{v}_q\| \|\mathbf{v}_i\|}$$

Retrieval yields candidates satisfying $\text{Sim}(e_q, e_i) \ge \theta_{\text{sim}}$, where $\theta_{\text{sim}} \in (0, 1)$ is the similarity threshold.

### Decision Matrix

$$\text{Action}(e_q, e_i) = \begin{cases} \text{Cold-Start Investigation}, & \text{if } \nexists e_i \text{ s.t. } \text{Sim}(e_q, e_i) \ge \theta_{\text{sim}} \\ \text{Seeded Investigation}, & \text{if } \Delta(S_{\text{curr}}, S_i) > 0 \\ \text{Contradiction Re-investigate}, & \text{if } \Delta(S_{\text{curr}}, S_i) = 0 \text{ and } \text{Outcome}(e_i) = \text{Approved} \\ \text{Pattern Reuse}, & \text{if } A_{\text{curr}} \neq A_i \text{ and } \text{Sim}(e_q, e_i) \ge \theta_{\text{pattern}} \\ \text{Full Investigation}, & \text{if } \text{Outcome}(e_i) = \text{Rejected} \end{cases}$$

---

##  Architecture

                          **INCIDENT** 
                                │ 
                                ▼ 
                    ┌───────────────────────┐ 
                    │                       │ 
                    ▼                       ▼ 
                **DATAHUB**           **CHROMADB** 
            (Semantic Memory)       (Episodic Memory) 
                    │                       │ 
            Schema,Lineage      Historical Freshness (Stale) Incidents 
                    │                       │ 
                    └───────────┬───────────┘ 
                                ▼ 
                          ┌─────────────┐ 
                          │     GROQ    │
                          │     (LLM)   │ 
                          └──────┬──────┘ 
                                 ▼ 
                          **INVESTIGATION** 
                                  │ 
                      ┌───────────┴───────────┐ 
                      ▼                       ▼ 
                 PROPOSED FIX         LEARNED LESSON 
                      │ 
                Human Approval                │
                      ▼                       ▼ 
                VERIFICATION                DATAHUB
              (Fix Applied)          (Knowledge Write-Back) 
                      |                       │  
                      └───────────┬───────────┘ 
                                  ▼ 
                          **NEW EXPERIENCE** 
                                  │ 
                                  ▼ 
                            **CHROMADB**
---

##  Quick Start

# Clone the repository 
git clone [https://github.com/Tomix-pyt/Datahub-Hackatohon-1.0.git]

cd Datahub-Hackatohon-1.0

# Create and activate a virtual environment

python3 -m venv .venv source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies

pip install --upgrade pip 

pip install -r requirements.txt

# Copy environment template

cp .env.example .env

# Run in mock mode (no API keys required)

python app.py

# Run tests

pytest tests/ -v 


⚙️ Configuration Create a .env file with the following variables:

|       Variable      |         Description                           |         Default            |
|       ---           |                      ---                      |           ---              |
| CORTEX_MOCK_MODE    | Run offline without live **API** calls        | true                       |
| DATAHUB_GMS_URL     | DataHub **GMS** endpoint                      | [http://localhost:**8080**]|
| DATAHUB_TOKEN       | DataHub Personal Access Token                 |""                          |
| GROQ_API_KEY        | Groq **API** key (for real **LLM** inference) |""                          |
| GROQ_MODEL          | Groq model choice                             | llama-3.3-70b-versatile    |
| CHROMA_PERSIST_DIR  | ChromaDB persistence directory                | ./data/chroma              |

 Running Cortex
Mode A: Mock Mode (Zero External Dependencies)
bash
# Baseline mock pipeline (Version 1 - Initial State)
CORTEX_MOCK_MODE=true CORTEX_MOCK_VERSION=v1 python app.py

# Run specific test modules

pytest tests/test_graph.py -v 

pytest tests/test_diff.py -v 

pytest tests/test_reflection.py -v

# Run with coverage

pytest tests/ -v --cov=cortex --cov-report=html 
# 📁 Project Structure text 
├──cortex/  |               
            ├── graph.py                # LangGraph control flow 
            ├── models.py               # Typed Pydantic contracts 
            ├── memory_episodic.py      # ChromaDB vector store 
            ├── memory_semantic.py      # DataHub **SDK** integration 
            ├── diff.py                 # Snapshot & lineage diffing 
            ├── reflection.py           # Recurrence guard & promotion gate 
            ├── llm.py                  # **LLM** interface (Groq + mock) 
            ├── pedure.py            # **YAML** runbook loader │ 
├── procedures/ │   
                ├── schema_drift.yaml   # Schema change runbook │   
                ├── freshness.yaml      # Freshness violation runbook │   
                └── default.yaml        # Fallback triage runbook │ 
tests/  │   
        ├── test_graph.py       # Warm/cold start tests │   
        ├── test_diff.py        # Snapshot equality tests │   
        └── test_reflection.py  # **HITL** approval/rejection tests │ 
├── requirements.txt
├── examples  # to store examples and logs from use
├── data/chroma # for chromaDB
├── scripts/
        └── simulate_incident.py, # to simulate schema drift and freshness error
├── .env.example            # Environment template 
├── app.py  # for mock mode
├── app_live.py # for live test, it needs an oss of datahub and datahub sdk though
└── **README**.md               # This file Built With Python 3.10+ – Core application logic.


# USed Stack
LangGraph – Stateful investigation orchestration.

DataHub – Semantic memory and knowledge write-back.

ChromaDB – Episodic experience memory.

Groq – Fast **LLM** inference for reasoning and diagnosis.

Pydantic – Type-safe data contracts.

pytest – Testing framework.

📄 License This project is licensed under the Apache License 2.0. See the **LICENSE** file for details.

🙏 Acknowledgments DataHub for providing the metadata platform that makes this possible.

LangChain/LangGraph for the agent orchestration framework.

Groq for lightning-fast **LLM** inference.

"Cortex should make every incident investigation make the next one easier.😂😂😂"