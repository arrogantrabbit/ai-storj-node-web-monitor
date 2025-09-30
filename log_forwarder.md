### Daemonizing the Log Forwarder (`log_forwarder.py`)

To ensure the log forwarder runs reliably in the background and starts automatically on boot, you should run it as a proper service. This guide provides instructions for both FreeBSD (`rc.d`) and modern Linux (`systemd`).

#### Step 1: Common Setup (Both FreeBSD & Linux)

These initial steps are the same for both operating systems. You must run these commands as `root`.

**1. Create a Dedicated User**

For security, the service should run under an unprivileged user. We'll create a system user named `storj` with no login shell.

*   **On FreeBSD:**
    ```bash
    pw user add storj -c "Storj Services User" -d /nonexistent -s /usr/sbin/nologin
    ```
*   **On Linux:**
    ```bash
    useradd --system --no-create-home --shell /bin/false storj
    ```

**2. Place the Application Files**

Store the `log_forwarder.py` script in a standard, non-user directory.

```bash
# Create the directory
mkdir -p /usr/local/storj_monitor

# Copy the forwarder script
cp /path/to/your/log_forwarder.py /usr/local/storj_monitor/

# Set the correct ownership
chown -R storj:storj /usr/local/storj_monitor
```

---

### Option A: FreeBSD `rc.d` Service

Follow these steps if your storagenode is running on FreeBSD.

**1. Create the `rc.d` Script**

Create the file `/usr/local/etc/rc.d/storj_forwarder`.

**File:** `/usr/local/etc/rc.d/storj_forwarder`
```sh
#!/bin/sh
#
# PROVIDE: storj_forwarder
# REQUIRE: NETWORKING DAEMON
# KEYWORD: shutdown
#
# Add the following to /etc/rc.conf to enable this service:
#
# storj_forwarder_enable="YES"
#
# Optional variables:
# storj_forwarder_user="storj"
# storj_forwarder_pyexec="/usr/local/bin/uv run"
# storj_forwarder_logfile="/var/log/storagenode.log"
# storj_forwarder_port="9999"
#

. /etc/rc.subr

name="storj_forwarder"
rcvar="${name}_enable"

load_rc_config ${name}

: ${storj_forwarder_enable:="NO"}
: ${storj_forwarder_user:="storj"}
: ${storj_forwarder_group:="${storj_forwarder_user}"}
: ${storj_forwarder_path:="/usr/local/storj_monitor/log_forwarder.py"}
: ${storj_forwarder_pyexec:="/usr/local/bin/uv run"}
: ${storj_forwarder_logfile:="/var/log/storagenode.log"} # Important: Check this path!
: ${storj_forwarder_host:="0.0.0.0"}
: ${storj_forwarder_port:="9999"}
: ${storj_forwarder_pidfile:="/var/run/${name}.pid"}
: ${storj_forwarder_output_log:="/var/log/${name}.log"}

pidfile="${storj_forwarder_pidfile}"
command="/usr/sbin/daemon"
command_args="-f -p ${pidfile} -u ${storj_forwarder_user} \
    -o ${storj_forwarder_output_log} \
    ${storj_forwarder_pyexec} ${storj_forwarder_path} \
    --log-file ${storj_forwarder_logfile} \
    --host ${storj_forwarder_host} \
    --port ${storj_forwarder_port}"

start_precmd()
{
    # Ensure the user running the service can read the log file
    if ! su -m "${storj_forwarder_user}" -c "test -r ${storj_forwarder_logfile}"; then
        echo "Error: User '${storj_forwarder_user}' cannot read the log file:"
        echo "${storj_forwarder_logfile}"
        echo "Please check file permissions."
        return 1
    fi
}

run_rc_command "$1"
```

**2. Make the Script Executable**
```bash
chmod +x /usr/local/etc/rc.d/storj_forwarder
```

**3. Configure and Enable the Service**

Edit `/etc/rc.conf` to enable the service and customize its parameters.

```sh
# Add these lines to /etc/rc.conf

# Enable the Storj Log Forwarder service
storj_forwarder_enable="YES"

# --- Customize if your setup differs from the defaults ---
#
# Set the correct path to your storagenode's log file
# storj_forwarder_logfile="/path/to/your/storagenode.log"
#
# Set the port you want the forwarder to listen on
# storj_forwarder_port="9001"
```

**4. Manage the Service**

You can now use the `service` command to manage the forwarder.
```bash
# Start the service
service storj_forwarder start

# Check its status
service storj_forwarder status

# Check the forwarder's own log output for errors
tail -f /var/log/storj_forwarder.log

# Stop the service
service storj_forwarder stop
```

---

### Option B: Linux `systemd` Service

Follow these steps if your storagenode is running on a modern Linux distribution (e.g., Debian, Ubuntu, CentOS, Fedora).

**1. Create the `systemd` Unit File**

Create the file `/etc/systemd/system/storj-forwarder.service`.

**File:** `/etc/systemd/system/storj-forwarder.service`
```ini
[Unit]
Description=Storj Log Forwarder
After=network.target

[Service]
# Service execution
Type=simple
User=storj
Group=storj
ExecStart=/usr/local/bin/uv run /usr/local/storj_monitor/log_forwarder.py --log-file ${LOG_FILE} --host ${LISTEN_HOST} --port ${LISTEN_PORT}
EnvironmentFile=/etc/default/storj-forwarder

# Process management
KillSignal=SIGINT
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**2. Create the Configuration File**

This file allows you to easily change the forwarder's settings without modifying the main service file.

Create the file `/etc/default/storj-forwarder`.

**File:** `/etc/default/storj-forwarder`
```sh
# Configuration for the Storj Log Forwarder service

# The full path to the storagenode log file to be monitored.
# The 'storj' user MUST have read access to this file.
LOG_FILE=/var/log/storagenode.log

# The host address for the forwarder to listen on.
# '0.0.0.0' means it will listen on all network interfaces.
LISTEN_HOST=0.0.0.0

# The TCP port the forwarder will listen on for connections from the monitor.
LISTEN_PORT=9999
```

**3. Check Permissions and Reload**

Ensure the `storj` user can read the log file. You may need to add it to a group with access (e.g., `adm`) or adjust permissions.

Then, tell `systemd` to load the new service definition.
```bash
# Example: Add 'storj' user to the 'adm' group (common for log access)
usermod -a -G adm storj

# Reload systemd
systemctl daemon-reload
```

**4. Manage the Service**

You can now use `systemctl` to manage the forwarder.

```bash
# Enable the service to start automatically on boot
systemctl enable storj-forwarder.service

# Start the service now
systemctl start storj-forwarder.service

# Check its status and recent logs
systemctl status storj-forwarder.service

# View the live log output from the forwarder
journalctl -u storj-forwarder.service -f

# Stop the service
systemctl stop storj-forwarder.service
```
