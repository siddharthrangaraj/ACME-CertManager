#!/usr/bin/env python3
import subprocess
import sys
import textwrap
from pathlib import Path
import time
import os

# ANSI color codes
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

def colored(text, color):
    return f"{color}{text}{Colors.RESET}"

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*60}")
    print(f"{text}")
    print(f"{'='*60}{Colors.RESET}\n")

def print_success(text):
    print(f"{Colors.GREEN}[✓]{Colors.RESET} {text}")

def print_warning(text):
    print(f"{Colors.YELLOW}[!]{Colors.RESET} {text}")

def print_error(text):
    print(f"{Colors.RED}[✗]{Colors.RESET} {text}")

def print_info(text):
    print(f"{Colors.BLUE}[i]{Colors.RESET} {text}")

def run(cmd, check=True, capture_output=False, color_output=True, shell=False, silent=False):
    if not silent:
        print(f"{Colors.BOLD}[CMD]${Colors.RESET} {colored(' '.join(cmd) if not shell else cmd, Colors.WHITE)}")
    result = subprocess.run(cmd, shell=shell, text=True, capture_output=capture_output)
    if result.returncode != 0 and check:
        print_error(f"Command failed with code {result.returncode}")
        if result.stderr:
            print(colored(result.stderr.strip(), Colors.RED))
        sys.exit(result.returncode)
    elif color_output and result.stdout and not silent:
        print(colored(result.stdout.strip(), Colors.GREEN))
    if capture_output:
        return result.stdout.strip()
    return ""

def confirm(prompt: str, default=True) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        ans = input(f"{colored(prompt, Colors.YELLOW)} [{d}]: ").strip().lower()
        if not ans: return default
        if ans in ("y", "yes"): return True
        if ans in ("n", "no"): return False
        print_warning("Please answer y or n.")

def ask_nonempty(prompt: str, default: str | None = None) -> str:
    while True:
        ans = input(f"{colored(prompt, Colors.CYAN)} [{default}]: " if default else f"{colored(prompt, Colors.CYAN)}: ").strip()
        if ans or default and not ans: return ans or default
        print_error("Value cannot be empty.")

def ask_namespace(prompt: str, default: str | None = None) -> str:
    while True:
        ns = ask_nonempty(prompt, default)
        if len(ns) > 63 or not ns[0].isalpha() or not all(c.isalnum() or c == '-' for c in ns):
            print_error("Invalid namespace name")
            continue
        return ns

def ask_install_method():
    print(colored("\nCert-Manager Deployment Method:", Colors.BOLD))
    print("  1. Helm ")
    print("  2. YAML manifests")
    while True:
        choice = ask_nonempty("Choose (1-2)", "1")
        if choice in ("1", "helm"):
            return "helm"
        elif choice in ("2", "yaml"):
            return "yaml"
        print_warning("Please choose 1 or 2")

def check_prerequisites():
    print_header("Prerequisites")
    for tool, cmd in [("kubectl", ["kubectl", "version", "--client"]), ("helm", ["helm", "version", "--short"])]:
        if not run(cmd, check=False, capture_output=True):
            print_error(f"{tool} not found")
            sys.exit(1)
        print_success(f"{tool} OK")

    if "is running at" not in run(["kubectl", "cluster-info"], check=False, capture_output=True):
        print_error("No Kubernetes cluster exists")
        sys.exit(1)
    print_success("All the prerequisites have been validated successfully.")

def get_all_cert_manager_resources():
    """CATCHES EVERYTHING - cluster-wide + all namespaces + webhooks"""
    all_resources = []

    cluster_resources = [
        "crd", "clusterrole", "clusterrolebinding", "apiservice",
        "mutatingwebhookconfiguration", "validatingwebhookconfiguration"
    ]

    print_info("Scanning CLUSTER-WIDE resources...")
    for res_type in cluster_resources:
        result = run(["kubectl", "get", res_type, "-o", "name"], check=False, capture_output=True, silent=True)
        for line in result.splitlines():
            line = line.strip()
            if line and 'cert-manager' in line.lower():
                all_resources.append(f"{line} (cluster)")

    print_info("Scanning all namespaces...")
    ns_list = run(["kubectl", "get", "ns", "-o", "jsonpath={.items[*].metadata.name}"], check=False, capture_output=True, silent=True).split()

    ns_scoped = ["role", "rolebinding", "serviceaccount", "configmap", "secret", "deployment", "statefulset", "daemonset", "job", "cronjob"]
    for ns in ns_list:
        for res_type in ns_scoped:
            result = run(["kubectl", "get", res_type, "-n", ns, "-o", "name"], check=False, capture_output=True, silent=True)
            for line in result.splitlines():
                line = line.strip()
                if line and 'cert-manager' in line.lower():
                    all_resources.append(f"{line} (ns:{ns})")

    print_info("Scanning cert-manager namespace specifically...")
    result = run(["kubectl", "get", "all", "-n", "cert-manager", "-o", "name"], check=False, capture_output=True, silent=True)
    for line in result.splitlines():
        line = line.strip()
        if line and 'cert-manager' in line.lower():
            all_resources.append(f"{line} (ns:cert-manager)")

    return sorted(list(set(all_resources)))

def delete_resource(resource_str):
    """Delete ANY resource type"""
    if "(cluster)" in resource_str:
        name = resource_str.split(' (')[0]
        print_info(f"  Deleting cluster resource: {name}")
        run(["kubectl", "delete", name, "--ignore-not-found=true", "--force"], check=False)
    elif "(ns:" in resource_str:
        name = resource_str.split(' (')[0]
        ns = resource_str.split('ns:')[1].split(')')[0]
        print_info(f"  Deleting namespaced resource: {name} in {ns}")
        run(["kubectl", "delete", name, "-n", ns, "--ignore-not-found=true", "--force"], check=False)

def uninstall_cert_manager_step_by_step():
    print_header("Uninstalling cert-manager")

    print_info("Checking cert-manager namespace...")
    ns_resources = run(["kubectl", "get", "all", "-n", "cert-manager", "-o", "name"], check=False, capture_output=True, silent=True)
    print_info(f"Namespace resources: {ns_resources.strip() or 'None found'}")

    resources = get_all_cert_manager_resources()
    if not resources:
        print_success("No cert-manager resources found!")
        
        run(["kubectl", "delete", "ns", "cert-manager", "--ignore-not-found=true"], check=False)
        return True

    print(colored(f"Found {len(resources)} cert-manager resources:", Colors.BOLD))
    for i, res in enumerate(resources, 1):
        print(f"  {i:2d}. {res}")

    if confirm(f"Are we good to proceed with deletion of {len(resources)} resources?", default=False):
        print_info("Uninstalling the resources")
        deleted_count = 0
        for res in resources:
            delete_resource(res)
            deleted_count += 1
            print_success(f"Deleted {deleted_count}/{len(resources)}")

        print_info("Final namespace cleanup...")
        run(["kubectl", "delete", "all", "--all", "-n", "cert-manager", "--force"], check=False)
        run(["kubectl", "delete", "ns", "cert-manager", "--force"], check=False)
        print_success("Cert-Manager has been uninstalled successfully!")
        return True
    return False

def uninstall_ingress_nginx_step_by_step():
    print_header("Uninstalling ingress-nginx")
    ns = "ingress-nginx"

    print_info(f"Checking namespace: {ns}")
    if "No resources found" in run(["kubectl", "get", "pods", "-n", ns], check=False, capture_output=True, silent=True):
        print_success("No ingress-nginx deployment found!")
        return True

    print(colored(f"Found ingress-nginx in namespace: {ns}", Colors.BOLD))

    steps = [
        ("Delete Helm release", lambda: run(["helm", "uninstall", "ingress-nginx", "-n", ns], check=False)),
        ("Delete all resources", lambda: run(["kubectl", "delete", "all", "--all", "-n", ns, "--force"], check=False)),
        ("Delete namespace", lambda: run(["kubectl", "delete", "ns", ns, "--force"], check=False))
    ]

    for step_name, step_func in steps:
        print_info(f"Step: {step_name}")
        step_func()
        print_success(f"{step_name} complete")
        time.sleep(2)

    print_success("ingress-nginx has been uninstalled successfully!")
    return True

def uninstall_metallb_step_by_step():
    print_header("MetalLB STEP-BY-STEP UNINSTALL")
    ns = "metallb-system"

    print_info(f"Checking namespace: {ns}")
    if "No resources found" in run(["kubectl", "get", "pods", "-n", ns], check=False, capture_output=True, silent=True):
        print_success("No MetalLB deployment found!")
        return True

    print(colored(f"Found MetalLB in namespace: {ns}", Colors.BOLD))

    steps = [
        ("Delete configs", lambda: run(["kubectl", "delete", "ipaddresspool,l2advertisement", "--all", "-n", ns], check=False)),
        ("Delete all resources", lambda: run(["kubectl", "delete", "all", "--all", "-n", ns, "--force"], check=False)),
        ("Delete namespace", lambda: run(["kubectl", "delete", "ns", ns, "--force"], check=False)),
        ("Delete CRDs", lambda: run(["kubectl", "delete", "crd", "ipaddresspools.metallb.io", "l2advertisements.metallb.io", "bgppeers.metallb.io", "communities.metallb.io", "--ignore-not-found=true"], check=False))
    ]

    for step_name, step_func in steps:
        print_info(f"Step: {step_name}")
        step_func()
        print_success(f"{step_name} complete")
        time.sleep(2)

    print_success("MetalLB FULLY UNINSTALLED!")
    return True

def validate_and_cleanup_cert_manager_resources(target_ns):
    print_header("Checking cert-manager Resources in All Namespaces")

    resources = get_all_cert_manager_resources()

    if not resources:
        print_success("NO cert-manager resources anywhere!")
        return True

    print(colored(f"Found {len(resources)} Conflicting Resources:", Colors.BOLD))
    for i, res in enumerate(resources, 1):
        print(f"  {i:2d}. {res}")

    if confirm(f"Delete All {len(resources)} resources? (Required for Helm)", default=True):
        print_info("Started to delete the resources.")
        for res in resources:
            delete_resource(res)

        print_info(f"Target namespace cleanup: {target_ns}")
        run(["kubectl", "delete", "all", "--all", "-n", target_ns, "--force"], check=False)
        run(["kubectl", "delete", "ns", target_ns, "--force"], check=False)

        print_success(f"NUKED {len(resources)} resources!")
        time.sleep(5)
        return True
    return False

def install_cert_manager():
    print_header("Cert-Manager v1.19.1 Deployment")
    ns = ask_namespace("Namespace", "cert-manager")

    if not validate_and_cleanup_cert_manager_resources(ns):
        print_error("Cleanup Required!")
        return

    method = ask_install_method()

    run(["kubectl", "create", "ns", ns], check=False)
    print_success("Environment cleanup has been completed!")

    if method == "helm":
        cmd = [
            "helm", "install", "cert-manager",
            "oci://quay.io/jetstack/charts/cert-manager",
            "--version", "v1.19.1",
            "--namespace", ns,
            "--create-namespace",
            "--set", "crds.enabled=true",
            "--set", "prometheus.enabled=false"
        ]
        run(cmd)
    else:  # yaml
        print_info("Applying the YAML manifests...")
        run(["kubectl", "apply", "-f", "https://github.com/cert-manager/cert-manager/releases/download/v1.19.1/cert-manager.yaml"])
        run(["kubectl", "label", "namespace", ns, "certmanager.k8s.io/disable-validation=true"])

    print_info("Waiting for pods...")
    run(["kubectl", "wait", "pod", "-n", ns, "--all", "--for=condition=Ready", "--timeout=300s"], check=False)
    print_success("cert-manager v1.19.1 LIVE!")
    run(["kubectl", "get", "all", "-n", ns])

def check_service_exists(service_name, namespaces):
    for ns in namespaces:
        if "No resources found" not in run(["kubectl", "get", "pods", "-n", ns], check=False, capture_output=True):
            return True, ns
    return False, None

def install_ingress_nginx():
    print_header("ingress-nginx")
    ns = ask_namespace("Namespace", "ingress-nginx")
    run(["kubectl", "create", "ns", ns], check=False)
    run(["helm", "repo", "add", "ingress-nginx", "https://kubernetes.github.io/ingress-nginx"], check=False)
    run(["helm", "repo", "update"])
    run(["helm", "upgrade", "--install", "ingress-nginx", "ingress-nginx/ingress-nginx",
         "--namespace", ns, "--create-namespace", "--set", "controller.ingressClassResource.name=nginx"])
    run(["kubectl", "wait", "pod", "-n", ns, "-l", "app.kubernetes.io/component=controller",
         "--for=condition=Ready", "--timeout=180s"], check=False)
    print_success("ingress-nginx ready!")
    run(["kubectl", "get", "all", "-n", ns])

def install_metallb():
    print_header("MetalLB v0.15.3")
    ns = "metallb-system"
    run(["kubectl", "create", "ns", ns], check=False)
    run(["kubectl", "apply", "-f", "https://raw.githubusercontent.com/metallb/metallb/v0.15.3/config/manifests/metallb-native.yaml"])
    run(["kubectl", "create", "secret", "generic", "memberlist", "--from-literal=secretkey=$(openssl rand -base64 128)", "-n", ns], shell=True, check=False)
    run(["kubectl", "wait", "pod", "-n", ns, "--all", "--for=condition=Ready", "--timeout=180s"], check=False)
    print_success("MetalLB ready!")

def configure_metallb_full():
    print_header("MetalLB Configuration")
    ns = "metallb-system"

    if "No resources found" in run(["kubectl", "get", "pods", "-n", ns], check=False, capture_output=True):
        print_error("MetalLB is not installed! Run option 3 first.")
        return

    print_success("MetalLB detected")
    print_info("Node network info:")
    run(["kubectl", "get", "nodes", "-o", "wide"])

    print_warning("CRITICAL: IP range MUST be:")
    print("   • Same Subnet as node Internal IPs above")
    #print("   • UNUSED (ping test first)")
    print("   • Examples: 192.168.1.240-192.168.1.250 or 192.168.10.0/24")

    ip_range = ask_nonempty("IP range/CIDR")
    print_info(f"Test your range is free: ping {ip_range.split('-')[0] if '-' in ip_range else ip_range.split('/')[0]}")

    print_info("Removing old configurations")
    run(["kubectl", "delete", "ipaddresspool,l2advertisement", "--all", "-n", ns], check=False)

    pool_yaml = f"""apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: default-pool
  namespace: {ns}
spec:
  addresses:
  - {ip_range}
"""
    pool_file = "/tmp/metallb-pool.yaml"
    with open(pool_file, "w") as f:
        f.write(pool_yaml)
    run(["kubectl", "apply", "-f", pool_file])
    print_success("IPAddressPool created")

    adv_yaml = f"""apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: default-adv
  namespace: {ns}
spec:
  ipAddressPools:
  - default-pool
"""
    adv_file = "/tmp/metallb-adv.yaml"
    with open(adv_file, "w") as f:
        f.write(adv_yaml)
    run(["kubectl", "apply", "-f", adv_file])
    print_success("L2Advertisement created")

    Path(pool_file).unlink(missing_ok=True)
    Path(adv_file).unlink(missing_ok=True)

    print_info("Verifying configuration")
    run(["kubectl", "get", "ipaddresspool,l2advertisement", "-n", ns])
    print_success("MetalLB has been configured successfully!")
    print_info("Validating: kubectl run nginx --image=nginx --service-type=LoadBalancer --port=80")

def show_detailed_status():
    print_header("Status of the cert-manager deployment")

    cm_status, cm_ns = check_service_exists("cert-manager", ["cert-manager"])
    print(f"{'OK' if cm_status else 'NOT FOUND'} cert-manager ({cm_ns or 'not found'})")

    ing_status, ing_ns = check_service_exists("ingress-nginx", ["ingress-nginx"])
    print(f"{'OK' if ing_status else 'NOT FOUND'} ingress-nginx ({ing_ns or 'not found'})")
    if ing_status:
        print(colored("ingress-nginx Services:", Colors.BOLD))
        run(["kubectl", "get", "svc", "-n", ing_ns])

    ml_status, ml_ns = check_service_exists("metallb", ["metallb-system"])
    print(f"{'OK' if ml_status else 'NOT FOUND'} MetalLB ({ml_ns or 'not found'})")

    pools = run(["kubectl", "get", "ipaddresspool", "-n", "metallb-system", "-o", "name"], check=False, capture_output=True)
    ads = run(["kubectl", "get", "l2advertisement", "-n", "metallb-system", "-o", "name"], check=False, capture_output=True)
    print(f"{'OK' if pools.strip() else 'NOT FOUND'} MetalLB IP Pools: {pools.strip() or 'None'}")
    print(f"{'OK' if ads.strip() else 'NOT FOUND'} MetalLB L2 Ads: {ads.strip() or 'None'}")

def uninstall_certmanager():
    print_header("Uninstallation of cert-manager:")
    print(colored("Choose what to uninstall:", Colors.BOLD))

    options = [
        "1. cert-manager only",
        "2. ingress-nginx only",
        "3. MetalLB only",
        "4. ALL components",
        "5. Cancel"
    ]

    for opt in options:
        print(f"  {opt}")

    choice = ask_nonempty("Choice (1-5)", "5")

    if choice == "1":
        uninstall_cert_manager_step_by_step()
    elif choice == "2":
        uninstall_ingress_nginx_step_by_step()
    elif choice == "3":
        uninstall_metallb_step_by_step()
    elif choice == "4":
        uninstall_cert_manager_step_by_step()
        uninstall_ingress_nginx_step_by_step()
        uninstall_metallb_step_by_step()
    elif choice == "5":
        print_success("Cancelled")
    else:
        print_warning("Invalid choice")

def main():
    print_header("Kubeadm On-Prem Cluster Deployment")
    check_prerequisites()

    while True:
        statuses = {
            'cert-manager': check_service_exists("cert-manager", ["cert-manager"])[0],
            'ingress-nginx': check_service_exists("ingress-nginx", ["ingress-nginx"])[0],
            'metallb': check_service_exists("metallb", ["metallb-system"])[0]
        }

        print(colored("STATUS:", Colors.BOLD))
        for name, status in statuses.items():
            print(f"  {'OK' if status else 'NOT FOUND'} {name}")

        options = [
            "Deploy cert-manager v1.19.1",
            "Deploy ingress-nginx Deployment",
            "Deploy MetalLB",
            "Setup MetalLB external IP Configuration",
            "Uninstall the cert-manager setup",
            "Status of the cert-manager deployment",
            "Exit"
        ]

        print_header("SELECT:")
        for i, opt in enumerate(options, 1):
            print(f"{Colors.BOLD}{i:2d}.{Colors.RESET} {opt}")

        choice = ask_nonempty("Choice", "6")
        if choice == "1":
            install_cert_manager()
        elif choice == "2":
            install_ingress_nginx()
        elif choice == "3":
            install_metallb()
        elif choice == "4":
            configure_metallb_full()
        elif choice == "5":
            uninstall_certmanager()
        elif choice == "6":
            show_detailed_status()
        elif choice == "7":
            print_success("Bye!"); break
        else:
            print_warning("Invalid")

        if not confirm("Continue?", False):
            break
        print()

if __name__ == "__main__":
    main()