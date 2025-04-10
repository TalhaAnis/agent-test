#!/usr/bin/env python3
import sys
import json
import os
from submodule import * 

REQUIRED_BINARIES = ["kubectl"]
REQUIRED_MODULES = []


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--describe":
            describe()
            sys.exit(0)
        elif sys.argv[1] == "--prompt":
            get_system_prompt()
            sys.exit(0)

    user_input = sys.argv[1]
    context = json.loads(sys.argv[2])
    kubeconfig = context.get("kubeconfig", os.path.expanduser("~/.kube/config"))

    if missing := check_dependencies():
        print(json.dumps({"response": "\n".join(missing), "retry": False, "error": True}))
        sys.exit(1)

    if not os.path.exists(kubeconfig):
        print(json.dumps({"response": "❌ kubeconfig not found", "retry": False, "error": True}))
        sys.exit(1)

    if user_input.strip().startswith("kubectl"):
        result = run_kubectl(user_input, kubeconfig)
    elif "restart" in user_input.lower():
        result = disambiguate_restart(user_input, kubeconfig)
    elif "pod" in user_input.lower():
        result = disambiguate_fuzzy_lookup("pod", user_input, kubeconfig)
    else:
        result = {
            "response": "⚠️ I'm not sure how to interpret that. Try asking to run a `kubectl` command or restart a resource.",
            "retry": True,
            "error": True
        }

    print(json.dumps(result))
