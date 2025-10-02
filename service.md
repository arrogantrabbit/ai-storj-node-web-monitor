# Service Installation for Storj Pro Monitor

This guide provides instructions for running the Storj Pro Monitor as a persistent background service (daemon) on FreeBSD and Linux systems. This is the recommended way to run the monitor on a dedicated server.

The core principles for both systems are:
1.  **Unprivileged User:** The service runs as a dedicated, non-root user (`storjmon`) for security.
2.  **Dedicated Directories:** Code, configuration, and data (database, GeoIP file) are kept in separate, standard locations.
3.  **Virtual Environment:** The application and its dependencies are installed into an isolated Python virtual environment to avoid conflicts with system packages.
4.  **Standard Service Management:** Use `service` (FreeBSD) or `systemctl` (Linux) to manage the application lifecycle.

---

### 1. FreeBSD `rc.d` Script

This setup uses a standard `rc.d` script to manage the service.

#### Step 1: Create User, Directories, and Install

First, we'll set up the user, directories, and install the application from its source (e.g., cloned from Git).

```bash
# 1. Create a dedicated user for the monitor service
pw user add storjmon -c "Storj Monitor User" -d /nonexistent -s /usr/sbin/nologin

# 2. Create a data directory for the database and GeoIP file
mkdir -p /var/db/storj_monitor
chown storjmon:storjmon /var/db/storj_monitor

# 3. Create a system-wide virtual environment
# (Assuming python3.9 is your desired version)
python3.9 -m venv /usr/local/share/storj_monitor_venv

# 4. Install the application into the virtual environment
# (Run this from your cloned project directory containing pyproject.toml)
/usr/local/share/storj_monitor_venv/bin/pip install .

# 5. Place the GeoIP database in the data directory
cp /path/to/your/GeoLite2-City.mmdb /var/db/storj_monitor/
chown storjmon:storjmon /var/db/storj_monitor/GeoLite2-City.mmdb
```

#### Step 2: Create the `rc.d` Script

Create the file `/usr/local/etc/rc.d/storj_monitor`. This script tells the system how to start and stop the service.

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

. /etc/rc.subr

name="storj_monitor"
rcvar="${name}_enable"

load_rc_config ${name}

# Set defaults for optional variables
: ${storj_monitor_enable:="NO"}
: ${storj_monitor_user:="storjmon"}
: ${storj_monitor_group:="storjmon"}
: ${storj_monitor_chdir:="/var/db/storj_monitor"} # Working directory for data
: ${storj_monitor_command:="/usr/local/share/storj_monitor_venv/bin/storj_monitor"}
: ${storj_monitor_nodes:=""} # This MUST be set in rc.conf
: ${storj_monitor_pidfile:="/var/run/${name}.pid"}
: ${storj_monitor_output_log:="/var/log/${name}.log"}

pidfile="${storj_monitor_pidfile}"
command="/usr/sbin/daemon"
# Run the command from the data directory so it can find its db and GeoIP file
command_args="-f -P ${pidfile} -u ${storj_monitor_user} \
    -o ${storj_monitor_output_log} \
    /bin/sh -c 'cd ${storj_monitor_chdir} && exec ${storj_monitor_command} ${storj_monitor_nodes}'"

start_precmd()
{
    if [ -z "${storj_monitor_nodes}" ]; then
        echo "Error: storj_monitor_nodes is not set in /etc/rc.conf."
        echo "Please define at least one node, e.g.:"
        echo 'storj_monitor_nodes="--node MyNode:/path/to/storagenode.log"'
        return 1
    fi
    # Ensure log file is writable
    touch ${storj_monitor_output_log}
    chown ${storj_monitor_user}:${storj_monitor_group} ${storj_monitor_output_log}
}

run_rc_command "$1"
```

#### Step 3: Configure and Enable

1.  **Make the script executable:**
    ```bash
    chmod +x /usr/local/etc/rc.d/storj_monitor
    ```

2.  **Edit `/etc/rc.conf`** to enable the service and define your nodes.
    ```sh
    # Enable the Storj Pro Monitor service
    storj_monitor_enable="YES"

    # Define the nodes to monitor. This is REQUIRED.
    # The entire string in quotes is passed as command-line arguments.
    storj_monitor_nodes="--node Node1:/var/log/node1.log --node Node2:192.168.1.101:9999"
    ```

#### Step 4: Manage the Service

You can now start, stop, and check the status like any other FreeBSD service.
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

For modern Linux distributions, `systemd` is the standard. This approach uses a unit file and a separate environment file for configuration.

#### Step 1: Create User, Directories, and Install

Run these commands as `root`.

```bash
# 1. Create a dedicated user for the monitor service
useradd --system --no-create-home --shell /bin/false storjmon

# 2. Create a data directory for the database and GeoIP file
mkdir -p /var/lib/storj_monitor
chown storjmon:storjmon /var/lib/storj_monitor

# 3. Create a system-wide virtual environment
# (This assumes a standard location for python3)
/usr/bin/python3 -m venv /usr/local/share/storj_monitor_venv

# 4. Install the application into the virtual environment
# (Run this from your cloned project directory containing pyproject.toml)
/usr/local/share/storj_monitor_venv/bin/pip install .

# 5. Place the GeoIP database in the data directory
cp /path/to/your/GeoLite2-City.mmdb /var/lib/storj_monitor/
chown storjmon:storjmon /var/lib/storj_monitor/GeoLite2-City.mmdb
```

#### Step 2: Create the `systemd` Unit File

Create a new file at `/etc/systemd/system/storj-monitor.service`.

**File:** `/etc/systemd/system/storj-monitor.service`
```ini
[Unit]
Description=Storj Pro Monitor Web Dashboard
After=network-online.target
Wants=network-online.target

[Service]
# Service execution
Type=simple
User=storjmon
Group=storjmon
WorkingDirectory=/var/lib/storj_monitor
EnvironmentFile=/etc/default/storj-monitor
ExecStart=/usr/local/share/storj_monitor_venv/bin/storj_monitor $STORJ_NODES

# Process management
KillSignal=SIGINT
TimeoutStopSec=30
Restart=on-failure
RestartSec=10

# Security hardening (optional but recommended)
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

#### Step 3: Create the Configuration File

`systemd` will load the node arguments from an environment file. This keeps the main unit file clean.

Create the file `/etc/default/storj-monitor`.

**File:** `/etc/default/storj-monitor`
```sh
# Configuration for the Storj Pro Monitor service
#
# Define all node arguments for the storj_monitor command.
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
