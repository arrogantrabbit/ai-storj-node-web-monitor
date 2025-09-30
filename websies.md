### 1. FreeBSD `rc.d` Script

This script is for running `websies.py` as a daemon on FreeBSD. It follows the same best practices as the forwarder script: it runs as an unprivileged user, manages a PID file, and can be fully configured from `/etc/rc.conf`.

#### Step 1: Create User and Place Files

If you haven't already, create the `storj` user and the application directory.

```bash
# Create a dedicated user (if not already done)
pw user add storj -c "Storj Services User" -d /nonexistent -s /usr/sbin/nologin

# Create the directory for all monitor files
mkdir -p /usr/local/storj_monitor

# Copy your application files
cp websies.py index.html GeoLite2-City.mmdb /usr/local/storj_monitor/

# Set correct ownership
chown -R storj:storj /usr/local/storj_monitor
```

#### Step 2: Create the `rc.d` Script

Create the file `/usr/local/etc/rc.d/storj_monitor`.

**File:** `/usr/local/etc/rc.d/storj_monitor`
```sh
#!/bin/sh
#
# PROVIDE: storj_monitor
# REQUIRE: NETWORKING DAEMON
# KEYWORD: shutdown
#
# Add the following to /etc/rc.conf to enable this service:
#
# storj_monitor_enable="YES"
# storj_monitor_nodes="--node Node1:/path/log --node Node2:host:port"
#
# Optional variables:
# storj_monitor_user="storj"
# storj_monitor_group="storj"
# storj_monitor_chdir="/usr/local/storj_monitor"  # Working directory
# storj_monitor_pyexec="/usr/local/bin/uv run"    # Python runner
# storj_monitor_path="websies.py"               # Script name (relative to chdir)
# storj_monitor_pidfile="/var/run/storj_monitor.pid"
# storj_monitor_output_log="/var/log/storj_monitor.log"
#

. /etc/rc.subr

name="storj_monitor"
rcvar="${name}_enable"

load_rc_config ${name}

# Set defaults for optional variables
: ${storj_monitor_enable:="NO"}
: ${storj_monitor_user:="storj"}
: ${storj_monitor_group:="storj"}
: ${storj_monitor_chdir:="/usr/local/storj_monitor"}
: ${storj_monitor_pyexec:="/usr/local/bin/uv run"}
: ${storj_monitor_path:="websies.py"}
: ${storj_monitor_nodes:=""} # This MUST be set in rc.conf
: ${storj_monitor_pidfile:="/var/run/${name}.pid"}
: ${storj_monitor_output_log:="/var/log/${name}.log"}

pidfile="${storj_monitor_pidfile}"
command="/usr/sbin/daemon"
# We wrap the actual command in "sh -c" to ensure it runs from the correct directory.
command_args="-f -p ${pidfile} -u ${storj_monitor_user} \
    -o ${storj_monitor_output_log} \
    /bin/sh -c 'cd ${storj_monitor_chdir} && ${storj_monitor_pyexec} ${storj_monitor_path} ${storj_monitor_nodes}'"

start_precmd()
{
    if [ -z "${storj_monitor_nodes}" ]; then
        echo "Error: storj_monitor_nodes is not set in /etc/rc.conf."
        echo "Please define at least one node, e.g.:"
        echo 'storj_monitor_nodes="--node MyNode:/path/to/storagenode.log"'
        return 1
    fi
}

run_rc_command "$1"
```

#### Step 3: Configure and Enable

1.  **Make the script executable:**
    ```bash
    chmod +x /usr/local/etc/rc.d/storj_monitor
    ```

2.  **Edit `/etc/rc.conf`** to enable the service and, most importantly, define your nodes.
    ```sh
    # Enable the Storj Pro Monitor service
    storj_monitor_enable="YES"

    # Define the nodes to monitor. This is REQUIRED.
    # The entire string in quotes is passed as command-line arguments.
    storj_monitor_nodes="--node Node1:/var/log/node1.log --node Node2:192.168.1.101:9999"
    ```

#### Step 4: Manage the Service

You can now start, stop, and check the status just like any other FreeBSD service.
```bash
# Start
service storj_monitor start

# Status
service storj_monitor status

# Stop
service storj_monitor stop
```

---

### 2. Linux `systemd` Service

For modern Linux distributions, `systemd` is the standard. This approach separates the service definition from its configuration, which is considered a best practice.

#### Step 1: Create User and Place Files

This is identical to the FreeBSD setup. Run these commands as `root`.

```bash
# Create a dedicated user (if not already done)
useradd --system --no-create-home --shell /bin/false storj

# Create the directory for all monitor files
mkdir -p /usr/local/storj_monitor

# Copy your application files
cp websies.py index.html GeoLite2-City.mmdb /usr/local/storj_monitor/

# Set correct ownership
chown -R storj:storj /usr/local/storj_monitor
```

#### Step 2: Create the `systemd` Unit File

Create a new file at `/etc/systemd/system/storj-monitor.service`.

**File:** `/etc/systemd/system/storj-monitor.service`
```ini
[Unit]
Description=Storj Pro Monitor Web Dashboard
Documentation=https://github.com/your-repo-link-here # Optional: Add your repo link
After=network-online.target
Wants=network-online.target

[Service]
# Service execution
Type=simple
User=storj
Group=storj
WorkingDirectory=/usr/local/storj_monitor
EnvironmentFile=/etc/default/storj-monitor
ExecStart=/usr/local/bin/uv run /usr/local/storj_monitor/websies.py $STORJ_NODES

# Process management
KillSignal=SIGINT
TimeoutStopSec=30
Restart=on-failure
RestartSec=10

# Security hardening (optional but recommended)
# NoNewPrivileges=true
# PrivateTmp=true
# ProtectSystem=strict
# ProtectHome=true

[Install]
WantedBy=multi-user.target
```

#### Step 3: Create the Configuration File

`systemd` will load the node arguments from an environment file. This keeps the main unit file clean and generic.

Create the file `/etc/default/storj-monitor`.

**File:** `/etc/default/storj-monitor`
```sh
# Configuration for the Storj Pro Monitor service
#
# Define all node arguments for the websies.py script.
# This entire string is passed as command-line arguments.
STORJ_NODES="--node Node1:/var/log/node1.log --node Node2:192.168.1.101:9999"

# To pass other arguments like --debug:
# STORJ_NODES="--node Node1:/var/log/node1.log --debug"
```

#### Step 4: Manage the Service

1.  **Reload `systemd`** to make it aware of the new unit file.
    ```bash
    systemctl daemon-reload
    ```

2.  **Enable the service** to start on boot.
    ```bash
    systemctl enable storj-monitor.service
    ```

3.  **Start and check the service:**
    ```bash
    # Start the service now
    systemctl start storj-monitor.service

    # Check its status
    systemctl status storj-monitor.service

    # View its logs (live)
    journalctl -u storj-monitor.service -f
    ```

4.  **To stop or restart:**
    ```bash
    systemctl stop storj-monitor.service
    systemctl restart storj-monitor.service
    ```

These service files provide a reliable, secure, and idiomatic way to manage the monitor application on both major server platforms.
