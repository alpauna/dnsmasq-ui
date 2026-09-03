# OPNsense router throughput on the Proxmox cluster

Investigation and fix from 2026-09-03. VMs on the cluster were getting
about 2 Gbps to the internet on a 5 Gbps symmetric Midco fiber line,
while a PC plugged straight into the fiber got the full 5 Gbps.

## Topology of the internet path

```
LAN VM  ->  host 10G NIC  ->  TP-Link JetStream switch (192.168.0.4, SFP+ ports 1/0/25-28)
        ->  pve06 (192.168.7.16, Ryzen 5 7600X, Broadcom bnx2x 10G)
        ->  OPNsense VM 106 "opnlan"  (routes LAN -> WAN)
        ->  back out on VLAN 999 (WAN) through the same switch  ->  ISP
```

- Gateway `192.168.0.1` is VM 106's `net0` (MAC `bc:24:11:d5:35:00`).
- WAN is VLAN 999, carried by the Proxmox SDN vnet `WanNet` (MTU 1500).
- VM 106 NICs: `net0` LAN (vmbr0), `net1` WAN (WanNet), `net2` VLAN 2,
  `net3` VLAN 7 (mgmt), `net5` VLAN 6.
- The switch and both hosts' 10G links were proven clean: iperf3 pve3
  to pve06 across the switch ran 9.9 Gbps in both directions.

## Root cause 1: GRO disabled on pve3's NICs (fixed)

`/etc/network/interfaces` on pve3 (192.168.7.13, Xeon E5-2683 v4, Intel
ixgbe) had this on eno1/eno2/eno3:

```
post-up /sbin/ethtool -K eno1 tso off gso off gro off lro off tx off rx off
```

With generic receive offload (GRO) off, every 1500-byte internet frame is
handled one at a time through the bridge and a single vhost thread on a
2.1 GHz core. Jumbo LAN traffic (MTU 8000) hid the problem because it is
five times fewer packets per second.

| 1500-byte-frame download into a pve3 VM | GRO off | GRO on |
|---|---|---|
| iperf3 from dns03 into VM 120 | 3.8 Gbps | 9.4 Gbps |
| Speedtest, builder VM via its direct WAN leg | 2623 down / 5170 up | 5160 down / 5159 up |

Fix applied: `ethtool -K eno1 gro on` live, and `gro off` changed to
`gro on` in the interfaces file for all three NICs. Backup at
`/etc/network/interfaces.bak-2026-09-03-gro`. The other offloads were
left off (the original reason for disabling them is unknown; upload
already reached line rate with them off). eno2's live setting picks up
`gro on` at the next boot or `ifreload`.

## Root cause 2: OPNsense VM had single-queue virtio NICs (fixed)

VM 106's NICs had no `queues=` option. Each virtio NIC then gets exactly
one `vhost` kernel thread on the host, so all packets for that NIC go
through one core. During every test through the router one
`vhost-<pid>` thread on pve06 sat at 97% CPU and throughput stalled at
2.0 to 3.0 Gbps regardless of direction.

| Through OPNsense, before | Result |
|---|---|
| Speedtest from VM 119 | 2024 down / 2437 up Mbps |
| iperf3 LAN to mgmt VLAN, either direction | 2.6 to 3.0 Gbps |

Fix applied on pve06 (VM 106 is HA-managed; `qm shutdown`/`qm start`
go through the HA manager):

```bash
cp /etc/pve/qemu-server/106.conf /root/106.conf.bak-2026-09-03
qm shutdown 106 --timeout 90
qm set 106 --net0 virtio=BC:24:11:D5:35:00,bridge=vmbr0,mtu=8000,queues=6
qm set 106 --net1 virtio=BC:24:11:2F:3B:32,bridge=WanNet,mtu=1,queues=6
qm set 106 --net2 virtio=BC:24:11:B1:EE:2C,bridge=VNet2,mtu=8000,queues=6
qm set 106 --net3 virtio=BC:24:11:4F:64:F9,bridge=vmbr0,mtu=8000,tag=7,queues=6
qm set 106 --net5 virtio=BC:24:11:40:03:0B,bridge=vmbr0,tag=6,queues=6
qm set 106 --cpu host
qm start 106
```

Notes:
- `queues=6` matches the VM's 6 vCPUs (Proxmox's recommendation).
- `firewall=1` was dropped from every NIC. There was no
  `/etc/pve/firewall/106.fw`, so the flag only added an extra bridge hop
  (`fwbr106iN`) per packet. OPNsense is the firewall.
- `cpu: host` exposes the 7600X's full instruction set to FreeBSD.
- Never change `queues` on the running router. Proxmox hot-replugs the
  NIC and FreeBSD can renumber `vtnetN`, which breaks OPNsense's
  interface assignments. Shut down, set, start. Downtime was ~16 s from
  `qm start` to internet reachable.
- After the change the host shows `Combined: 6` on `tap106i0..i3` and 30
  vhost threads for the VM instead of 5.

Results after the change are in the "Results" section below.

## How to measure

The speedtester VMs are 119 (`192.168.1.112`) and 120 (`192.168.1.99`),
Debian 13 on pve3, user `debian`, key `~/.ssh/id-rsa`. The test the
owner uses is the Ookla CLI against the Midco Sioux Falls server:

```bash
speedtest -s 4324
```

Useful A/B rig, all on the same host and NIC as the speedtesters:

- **Bypass the router**: the builder VM 118 (`192.168.0.27`) has `eth1`
  directly on WAN VLAN 999. `speedtest -s 4324 -I eth1` measures what
  the host and ISP can do with OPNsense out of the path.
- **Router only, no ISP**: dns03 (`192.168.0.233`) has a VLAN 7 leg at
  `192.168.7.233`. `iperf3 -s` there and `iperf3 -c 192.168.7.233 -P 4`
  from a LAN VM forces the traffic through OPNsense.
- **Switch only**: `iperf3` between two Proxmox hosts on the mgmt subnet
  (e.g. pve3 to pve06). The host firewall blocks port 5201 from the LAN,
  so this only works host to host.
- **Where is the CPU going**: on the host,
  `top -H -p $(cat /var/run/qemu-server/106.pid)` while a test runs. A
  `vhost-*` thread near 100% means a single-queue NIC; a `CPU N/KVM`
  thread near 100% means the guest itself is the limit.

iperf3 is installed on the pve3 and pve06 hosts, VMs 119 and 120, and
dns03. Speedtest CLI is on 119, 120 and builder.

## Inside OPNsense (checked 2026-09-03 after the multiqueue change)

Access: root over SSH at the LAN address `192.168.0.1`, credentials in
the repo's `.env` under `#OPNsense info` (the repo SSH key is not
accepted). Root's login shell is `csh`, so pipe scripts into `/bin/sh -s`
rather than passing bash syntax directly.

Found:

| Item | State |
|---|---|
| Version | OPNsense 26.7.1_1 on FreeBSD 15.1, 6 vCPU, `cpu: host` visible as Ryzen 7600X |
| Multiqueue | `dev.vtnet.0..3.act_vq_pairs: 6` on LAN, WAN, VLAN 2, VLAN 7. Active. |
| `hw.vtnet.mq_disable` | 0 (good) |
| `net.inet.rss.enabled` | 0, `net.isr.maxthreads` 1, `net.isr.dispatch` direct |
| Hardware offloads | checksum, TSO, LRO, VLAN filter all disabled (OPNsense defaults). `ifconfig -m vtnet0` shows no CSUM capability because the loader tunable `hw.vtnet.csum_disable=1` is set by that GUI option. |
| pf | 133 rules, ~700 states, no shaper, no IDS. Not a factor. |
| CPU during a 4.3 Gbps download | busiest core 46% interrupt; only two vtnet queue IRQs carried traffic (41% and 26%), the other four idle |

Conclusion: the guest is no longer CPU-bound. The remaining gap (4.2 to
4.3 Gbps routed versus 5.2 Gbps bypassing the router, on IPv4 and IPv6
alike) comes from traffic concentrating on two queues rather than six.

Not yet applied, needs one more reboot (loader tunables cannot be set
at runtime). Put these in `/boot/loader.conf.local` (OPNsense preserves
that file across upgrades; deleting it reverts) or add them as System >
Settings > Tunables:

```
net.inet.rss.enabled="1"
net.inet.rss.bits="3"
net.isr.maxthreads="-1"
net.isr.bindthreads="1"
```

Deliberately left alone: hardware checksum offload. Enabling it on
virtio has a history of pf/vtnet checksum bugs, and the guest is not
CPU-bound, so the risk is not worth it right now.

## Side findings

- LAN IPv6 is not working end to end. The router's LAN interface tracks
  the WAN prefix (`2605:4a80:b004:4070::/64`), but VM 119 holds an
  address from a different prefix (`2605:4a80:b000:f800::/64`) and has
  no IPv6 default route, so `speedtest` over IPv6 fails with "Network is
  unreachable". Something else on the LAN is advertising the old prefix.
  Separate issue, not touched.
- `tap106i5` (VLAN 6, `net5`) shows `Combined: 1` on the host and
  `act_vq_pairs: 0` in the guest; that interface is not assigned in
  OPNsense, so it never negotiated queues. Harmless.
- The builder VM's WAN leg (`eth1`) cannot `bind()` its IPv4 address for
  `speedtest -i`; use `-I eth1` and, to force IPv4, temporarily set
  `net.ipv6.conf.eth1.disable_ipv6=1` (SLAAC restores the address within
  a minute after re-enabling).

## Side effect of the reboot: the LAN IPv6 prefix changed

Midco hands out a new delegated /60 whenever OPNsense releases its
DHCPv6 lease. The router's own dhcp6c log shows the history:

| When | Delegated prefix |
|---|---|
| 2026-08-13 to 08-19 | `2605:4a80:b004:b120::/60` |
| 2026-08-23 02:14 | `2605:4a80:b000:f800::/60` |
| 2026-09-03 03:12 (the multiqueue reboot) | `2605:4a80:b004:4070::/60` |

WAN has "Prevent release" off (`dhcp6_norelease` = 0), so a reboot sends a
DHCPv6 Release and the ISP assigns a fresh prefix. Runtime re-requests
the same day (03:16, 03:19) got the same prefix back, so it is the
release-on-shutdown that costs the prefix. Turning on Interfaces > WAN >
"Prevent release" is the mitigation; the DUID is stable (Dec 2025).

Consequences seen on 2026-09-03:
- dnsmasq-ui's subnet tracking moved the `lan` subnet and every tracked
  host AAAA record to `b004:4070` automatically (log 08:22:56 on dns31).
- The keepalived IPv6 VIP (`2605:4a80:b004:b120::230`) and its AAAA
  record `dns.ad.alshowto.com` are not tracked, and the dashboard emailed
  its "IPv6 VIP prefix has drifted" notice. That VIP had in fact already
  been stale since the 08-23 renumber. Remediation is the manual
  procedure from the email (rewrite `virtual_ipaddress` on all three DNS
  servers, reload keepalived, update `keepalive_vip6` and the AAAA record,
  redeploy). The repo's `ansible/dnsmasq-setup.yml` default and README
  were updated to `2605:4a80:b004:4070::230`, and the manual procedure is
  now automated (next bullet).
- "Prevent release" was enabled on 2026-09-03 (`dhcp6_norelease` = 1 in
  the OPNsense interface settings model, applied by regenerating
  `/var/etc/dhcp6c.conf` so `#EXTRAOPTS=-d -n`, then `kill -9` on the old
  dhcp6c so no Release went out, and starting it again by hand). The new
  dhcp6c re-requested and Midco returned the same `b004:4070::/60`. A
  lease expiry or ISP maintenance can still renumber, so dnsmasq-ui now
  auto-tracks the IPv6 VIP (see README, "IPv6 VIP"): on a `lan` prefix
  change it rewrites keepalived on all three DNS servers, moves the
  `dns.ad.alshowto.com` AAAA, redeploys and emails a summary.
  keepalived also carries a link-local VIP (`fe80::230`) that is immune
  to all of this.

## Results

All numbers from VM 119 on pve3 through OPNsense, same `-s 4324` server.

| Test | Before | After GRO + multiqueue |
|---|---|---|
| Speedtest download | 2024 Mbps | 4218 to 4291 Mbps |
| Speedtest upload | 2437 Mbps | 3311 to 4504 Mbps |
| iperf3 LAN to VLAN 7, 4 streams | 2.77 Gbps | 6.92 Gbps |
| iperf3 VLAN 7 to LAN, 4 streams | 2.61 Gbps | 6.14 Gbps |
| iperf3 single stream | 3.00 Gbps | 4.09 Gbps |
| Busiest thread on pve06 during test | one vhost at 97% | vCPUs 40-53% each, vhost 35-54% |

Reference, router bypassed (builder VM `eth1`, same host as VM 119):
5160/5159 over IPv6, 5225/5103 over IPv4. The pve06 uplink also passed
9.8 Gbps in both directions simultaneously, so it is not the limit.
