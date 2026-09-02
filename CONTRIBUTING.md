# Contributing to Lot Vulture

First off, thank you for considering contributing to Lot Vulture! It's people like you that make Lot Vulture such a great tool for the community.

By contributing to this project, you agree to abide by its terms and the [Contributor License Agreement (CLA)](./CLA.md).

## 📜 License

Lot Vulture is licensed under the **Lot Vulture Source License 1.0.0 (Adapted from PolyForm Shield License 1.0.0)**. It permits broad use, but includes a non-compete condition that prohibits using the software to build or distribute competing products or services. See [LICENSE.md](./LICENSE.md) for full terms.

## ✍️ Contributor License Agreement (CLA)

To ensure that Lot Vulture can remain sustainable and provide long-term support and potential commercial offerings, we require all contributors to sign our **Individual Contributor License Agreement**. 

This agreement clarifies that you grant us a permanent, irrevocable license to use and distribute your contributions. You retain ownership of your code, but you give us the rights needed to manage the project effectively.

Please read the full **[CLA.md](./CLA.md)** before submitting a Pull Request.

**How to Sign:** When you open your first Pull Request, the automated CLA bot will comment on your PR asking you to sign. Simply reply with:
```text
I have read the CLA Document and I hereby sign the CLA
```
The bot will record your signature in `signatures/cla-signatures.json` and mark the PR check as passed.

## 🚀 How Can I Contribute?

### Reporting Bugs
*   Check if the bug has already been reported in the Issues.
*   If not, open a new issue. Include as much detail as possible:
    *   OS and Python version.
    *   Hardware (CPU vs. GPU/CUDA).
    *   Steps to reproduce.
    *   Logs (from `logs/vulture.log`).

### Suggesting Enhancements
*   Open an issue with the "enhancement" tag.
*   Describe the use case and why this feature would be valuable.

### Pull Requests
1.  Fork the repo and create your branch from `master`.
2.  If you've added code that should be tested, add tests!
3.  If you've changed APIs, update the documentation.
4.  Ensure the test suite passes locally:
    ```bash
    # Backend tests
    pytest

    # Frontend tests
    cd frontend && npm test -- --run
    ```
5.  Make sure your code lints and builds cleanly (`npm run build`).
6.  Open the Pull Request. GitHub Actions will automatically run the full CI test suite.

## 🛠️ Development Standards

### Backend (Python/FastAPI)
*   **Style**: Adhere to PEP8.
*   **Types**: Use Python type hints everywhere.
*   **Pydantic**: We use Pydantic 2.0. Use `model_config` and `model_dump()`.
*   **SQLAlchemy**: We use SQLAlchemy 2.0 (DeclarativeBase).
*   **Logging**: Use the centralized `logger` from `backend.logging_config`.

### Frontend (Vue 3/Vuetify)
*   **Style**: Use the Script Setup (`<script setup>`) syntax.
*   **UI**: Follow Material Design patterns via Vuetify 3.
*   **Responsive**: Ensure new components look good on mobile and desktop.

### Testing
We use `pytest` for backend testing and `vitest` for frontend testing. All contributions that add logic must include corresponding tests.

```bash
# Run all backend tests
pytest

# Run all frontend tests
cd frontend && npm test -- --run
```

---

Thank you for helping us circle the lot!
