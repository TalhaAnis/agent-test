import sys
import shutil
import subprocess
import shlex
import re
import json


REQUIRED_BINARIES = ["kubectl"]
REQUIRED_MODULES = []

def check_dependencies():
    missing = []
    for binary in REQUIRED_BINARIES:
        if not shutil.which(binary):
            missing.append(f"❌ Missing required binary: {binary}")
    for module in REQUIRED_MODULES:
        try:
            __import__(module)
        except ImportError:
            missing.append(f"❌ Missing required Python module: {module}")
    return missing
def describe():
    info = {
        "name": "k8s",
        "description": (
            "(v1.0.1) This is an Intelligent Kubernetes skill that executes kubectl commands. "
            "Supports pods, deployments, services, etc. with fuzzy matching, LLM-powered inference, and safe disambiguation."
        ),
        "commands": [],
        "context": {
            "kubeconfig": "Path to your kubeconfig file"
        }
    }
    print(json.dumps(info, indent=2))

def get_system_prompt():
    prompt = (
        "You are a Kubernetes AI agent with access to a skill named 'k8s' that can execute real `kubectl` commands.\n"
        "When the user asks something Kubernetes-related, respond with:\n"
        "  use:k8s kubectl <command>\n"
        "\n"
        "If the user mentions a vague name like 'loki', 'neuvector', or anything else, try to infer the most likely Kubernetes resource type — such as a pod, deployment, service, statefulset, etc.\n"
        "Use fuzzy matching to find real resource names across all namespaces.\n"
        "If there are multiple matching results, show the names and namespaces, and ask the user to clarify.\n"
        "If you're unsure of the resource type, say something like:\n"
        "  'It could be a deployment, pod, or another Kubernetes resource. Which one should I check for?'\n"
        "\n"
        "Use the correct kubectl command for the resource type:\n"
        "  • rollout restart deployment ...\n"
        "  • rollout restart daemonset ...\n"
        "  • rollout restart statefulset ...\n"
        "  • delete pod ...\n"
        "  • get svc ...\n"
        "\n"
        "NEVER include placeholders like <pod-name> or <namespace>. Always use real, ready-to-run values based on recent context or listings.\n"
        "\n"
        "If the output is a number or a simple result (e.g., '53'), respond with a natural sentence like:\n"
        "  'There are currently 53 running pods in your cluster.'\n"
        "\n"
        "If the user input is not related to Kubernetes, respond like a normal helpful chatbot.\n"
        "\n"
        "⚠️ When suggesting a command, always use the namespace that was found in the previous matching resource (e.g., if 'tempo-0' is in 'observability', do not default to 'default').\n"
        "⚠️ Never assume the namespace is 'default'. Always use the namespace previously shown for a pod or resource. If unsure, query it before suggesting a command. If you can infer it based on the interaction with the user do so\n"
        "⚠️ If a command fails, read the stderr and revise the command using the provided context.\n"
    )
    print(json.dumps({"system_prompt": prompt}))


def extract_named_resources(cmd, kubeconfig):
    try:
        result = subprocess.check_output(
            shlex.split(cmd),
            stderr=subprocess.STDOUT,
            env={"KUBECONFIG": kubeconfig},
            text=True
        )
        resources = []
        for line in result.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                resources.append((parts[1], parts[0]))  # name, namespace
        return resources
    except subprocess.CalledProcessError:
        return []

def get_all_resources(resource_type, kubeconfig):
    return extract_named_resources(f"kubectl get {resource_type} -A --no-headers", kubeconfig)

def fuzzy_match(term, items):
    return [(n, ns) for n, ns in items if term.lower() in n.lower()]

def get_pod_owner(pod_name, namespace, kubeconfig):
    try:
        cmd = f"kubectl get pod {pod_name} -n {namespace} -o json"
        output = subprocess.check_output(
            shlex.split(cmd),
            env={"KUBECONFIG": kubeconfig},
            text=True
        )
        data = json.loads(output)
        owner = data.get("metadata", {}).get("ownerReferences", [{}])[0]
        return owner.get("kind"), owner.get("name")
    except Exception:
        return None, None

def run_kubectl(raw_command, kubeconfig, resource_type=None, resource_name=None, namespace=None):
    if "<" in raw_command or ">" in raw_command:
        return {
            "response": "⚠️ Command contains a placeholder like <pod-name>. Please provide a valid name.",
            "retry": True,
            "error": True,
            "context": {
                "failed_command": raw_command,
                "stderr": "Detected placeholder characters <>",
                "resource_type": resource_type,
                "resource_name": resource_name,
                "namespace": namespace
            }
        }

    try:
        full_command = f"kubectl {raw_command[len('kubectl '):]}" if raw_command.startswith("kubectl") else raw_command
        print(f"🏃 Executing this command: {full_command}", file=sys.stderr)

        output = subprocess.check_output(
            full_command,
            shell=True,
            stderr=subprocess.STDOUT,
            env={"KUBECONFIG": kubeconfig},
            text=True
        ).strip()

        return {
            "response": output or "🤔 Command executed but returned no output.",
            "retry": False,
            "context": {
                "executed_command": full_command,
                "resource_type": resource_type,
                "resource_name": resource_name,
                "namespace": namespace
            }
        }

    except subprocess.CalledProcessError as e:
        context = {
            "failed_command": raw_command,
            "stderr": e.output.strip(),
            "resource_type": resource_type,
            "resource_name": resource_name,
            "namespace": namespace
        }

        if resource_type == "pod" and resource_name and namespace:
            owner_kind, owner_name = get_pod_owner(resource_name, namespace, kubeconfig)
            if owner_kind:
                context["owner"] = {
                    "kind": owner_kind,
                    "name": owner_name
                }

        return {
            "response": f"❌ Command failed:\n```\n{raw_command}\n```\nError:\n```\n{e.output.strip()}\n```\nCan you suggest a corrected command?",
            "retry": True,
            "error": True,
            "context": context
        }

def disambiguate_restart(user_input, kubeconfig):
    match = re.search(r"restart .*?([\w-]+)", user_input.lower())
    if not match:
        return {
            "response": "⚠️ I couldn't find a resource name in your restart request.",
            "retry": True,
            "error": True
        }

    term = match.group(1)

    resource_types = {
        "deployment": "rollout restart deployment {name} -n {namespace}",
        "daemonset": "rollout restart daemonset {name} -n {namespace}",
        "statefulset": "rollout restart statefulset {name} -n {namespace}",
        "pod": "delete pod {name} -n {namespace}"
    }

    for rtype, cmd_template in resource_types.items():
        matches = fuzzy_match(term, get_all_resources(rtype, kubeconfig))
        if matches:
            if len(matches) == 1:
                name, ns = matches[0]
                return run_kubectl(
                    f"kubectl {cmd_template.format(name=name, namespace=ns)}",
                    kubeconfig,
                    resource_type=rtype,
                    resource_name=name,
                    namespace=ns
                )
            else:
                suggestions = [f"{n} (namespace: {ns})" for n, ns in matches]
                return {
                    "response": f"🔎 I found multiple {rtype}s matching '{term}':\n" + "\n".join(suggestions),
                    "retry": True,
                    "context": {
                        "resource_type": rtype,
                        "fuzzy_term": term,
                        "matches": suggestions
                    }
                }

    return {
        "response": f"🤔 I couldn't find any restartable resource matching '{term}'.",
        "retry": True,
        "error": True,
        "context": {
            "resource_type": "unknown",
            "fuzzy_term": term
        }
    }

def disambiguate_fuzzy_lookup(resource_type, term, kubeconfig):
    matches = fuzzy_match(term, get_all_resources(resource_type, kubeconfig))
    if not matches:
        return {
            "response": f"⚠️ No {resource_type}s found matching '{term}'.",
            "retry": True,
            "error": True
        }
    elif len(matches) == 1:
        name, ns = matches[0]
        return run_kubectl(
            f"kubectl describe {resource_type} {name} -n {ns}",
            kubeconfig,
            resource_type=resource_type,
            resource_name=name,
            namespace=ns
        )
    else:
        suggestions = [f"{n} (namespace: {ns})" for n, ns in matches]
        return {
            "response": f"🔍 Multiple {resource_type}s match '{term}'. Please clarify:\n" + "\n".join(suggestions),
            "retry": True,
            "context": {
                "resource_type": resource_type,
                "fuzzy_term": term,
                "matches": suggestions
            }
        }
