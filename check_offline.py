"""Real GPU inference with Python socket connections blocked; synthetic text only."""
import json
import socket
from pathlib import Path
from unittest.mock import patch

from clinical_core.mlx_reader import MLXGenerator, validate_output


def main():
    root = Path(__file__).resolve().parent
    model = MLXGenerator(root / "models/qwen3-4b")
    sentences = [{"text": "Patient declined atorvastatin after discussing the benefits and risks.",
                  "date": "2026-09-01", "prev": ""}]
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Outbound network connection blocked")), \
         patch.object(socket.socket, "connect_ex", side_effect=AssertionError("Outbound network connection blocked")):
        raw, stats = model(sentences)
    output = validate_output(raw, sentences)
    result = {"synthetic": True, "python_socket_connections_blocked": True, "output": output, "stats": stats,
              "limitation": "Python socket guard, not an operating-system network sandbox or clinical validation"}
    (root / "artifacts/offline-smoke.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
