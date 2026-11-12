"""Closed surface for the rest of the stdlib queue, paths, io, and the island.

Names that are not listed stay fences. Constants are read from the lowering
interpreter so a baked `sys.platform` matches the CPython oracle on this host,
except `sys.version`: the version string describes the target the program
claims to be, not the machine that compiled it, so a 3.11-3.15 tool host
bakes the target's answer.
"""

from __future__ import annotations

import socket
import ssl
import stat
import sys
from typing import Any

from pyscriptc.builtins import RESERVED_EXCEPTIONS
from pyscriptc.sigrec import Sig, T, sig, sig_for, t_class, t_leaf, t_list, type_to_dict

_I: T = t_leaf("int")
_F: T = t_leaf("float")
_B: T = t_leaf("bool")
_S: T = t_leaf("str")
_BY: T = t_leaf("bytes")
_N: T = t_leaf("none")
_LS: T = t_list(_S)
_LLS: T = t_list(_LS)
_DATE: T = t_class("Date")
_DT: T = t_class("DateTime")
_TOD: T = t_class("Time")
_DELTA: T = t_class("TimeDelta")
_STM: T = t_class("StructTime")
_PATH: T = t_class("Path")
_STAT: T = t_class("Stat")
_TEXT: T = t_class("TextIO")
_BIN: T = t_class("BytesIO")
_STR_LIST: T = t_list(_S)


def _plain_fields(*names: str) -> list[tuple[str, T]]:
    """`(name, int)` rows: most synthetic classes are flat int records."""
    return [(name, _I) for name in names]


_MISSING: Any = object()


def _host_attr(owner: Any, name: str) -> Any:
    """`getattr(owner, name)`, or `_MISSING` when this host does not define it.

    Many surface names are platform specific: EADV, CLONE_* and AF_CAN are Linux
    only, and pathlib re-exports errno names on Linux but not on macOS. CPython
    raises AttributeError for an absent name, so the table leaves it out instead
    of failing the import on hosts that lack it.
    """
    return getattr(owner, name, _MISSING)


_ERRNO = (
    "EACCES",
    "EAGAIN",
    "EBUSY",
    "ECHILD",
    "EEXIST",
    "EINTR",
    "EINVAL",
    "EIO",
    "EISDIR",
    "ENOENT",
    "ENOSPC",
    "ENOTDIR",
    "ENOTEMPTY",
    "ENOTTY",
    "EPERM",
    "EPIPE",
    "EROFS",
    "ESRCH",
    "EWOULDBLOCK",
    "EXDEV",
)


# Harness probes whose body is `return module.NAME`, whose CPython value is an
# int that fits in signed 64-bit, on a module the 043 snapshot already marks
# lowered. Values are read from this interpreter in `_load_constants`.
_INT_SURFACE: dict[str, tuple[str, ...]] = {
    "calendar": (
        "MONDAY",
        "TUESDAY",
        "WEDNESDAY",
        "THURSDAY",
        "FRIDAY",
        "SATURDAY",
        "SUNDAY",
    ),
    "zipfile": (
        "ZIP_STORED",
        "ZIP_DEFLATED",
        "ZIP_BZIP2",
        "ZIP_LZMA",
    ),
    "base64": (
        "MAXBINSIZE",
        "MAXLINESIZE",
    ),
    "csv": (
        "QUOTE_ALL",
        "QUOTE_MINIMAL",
        "QUOTE_NONE",
        "QUOTE_NONNUMERIC",
        "QUOTE_NOTNULL",
        "QUOTE_STRINGS",
    ),
    "ctypes": (
        "DEFAULT_MODE",
        "RTLD_GLOBAL",
        "RTLD_LOCAL",
        "SIZEOF_TIME_T",
    ),
    "datetime": (
        "MAXYEAR",
        "MINYEAR",
    ),
    "errno": (
        "E2BIG",
        "EADDRINUSE",
        "EADDRNOTAVAIL",
        "EADV",
        "EAFNOSUPPORT",
        "EALREADY",
        "EBADE",
        "EBADF",
        "EBADFD",
        "EBADMSG",
        "EBADR",
        "EBADRQC",
        "EBADSLT",
        "EBFONT",
        "ECANCELED",
        "ECHRNG",
        "ECOMM",
        "ECONNABORTED",
        "ECONNREFUSED",
        "ECONNRESET",
        "EDEADLK",
        "EDEADLOCK",
        "EDESTADDRREQ",
        "EDOM",
        "EDOTDOT",
        "EDQUOT",
        "EFAULT",
        "EFBIG",
        "EHOSTDOWN",
        "EHOSTUNREACH",
        "EHWPOISON",
        "EIDRM",
        "EILSEQ",
        "EINPROGRESS",
        "EISCONN",
        "EISNAM",
        "EKEYEXPIRED",
        "EKEYREJECTED",
        "EKEYREVOKED",
        "EL2HLT",
        "EL2NSYNC",
        "EL3HLT",
        "EL3RST",
        "ELIBACC",
        "ELIBBAD",
        "ELIBEXEC",
        "ELIBMAX",
        "ELIBSCN",
        "ELNRNG",
        "ELOOP",
        "EMEDIUMTYPE",
        "EMFILE",
        "EMLINK",
        "EMSGSIZE",
        "EMULTIHOP",
        "ENAMETOOLONG",
        "ENAVAIL",
        "ENETDOWN",
        "ENETRESET",
        "ENETUNREACH",
        "ENFILE",
        "ENOANO",
        "ENOBUFS",
        "ENOCSI",
        "ENODATA",
        "ENODEV",
        "ENOEXEC",
        "ENOKEY",
        "ENOLCK",
        "ENOLINK",
        "ENOMEDIUM",
        "ENOMEM",
        "ENOMSG",
        "ENONET",
        "ENOPKG",
        "ENOPROTOOPT",
        "ENOSR",
        "ENOSTR",
        "ENOSYS",
        "ENOTBLK",
        "ENOTCONN",
        "ENOTNAM",
        "ENOTRECOVERABLE",
        "ENOTSOCK",
        "ENOTSUP",
        "ENOTUNIQ",
        "ENXIO",
        "EOPNOTSUPP",
        "EOVERFLOW",
        "EOWNERDEAD",
        "EPFNOSUPPORT",
        "EPROTO",
        "EPROTONOSUPPORT",
        "EPROTOTYPE",
        "ERANGE",
        "EREMCHG",
        "EREMOTE",
        "EREMOTEIO",
        "ERESTART",
        "ERFKILL",
        "ESHUTDOWN",
        "ESOCKTNOSUPPORT",
        "ESPIPE",
        "ESRMNT",
        "ESTALE",
        "ESTRPIPE",
        "ETIME",
        "ETIMEDOUT",
        "ETOOMANYREFS",
        "ETXTBSY",
        "EUCLEAN",
        "EUNATCH",
        "EUSERS",
        "EXFULL",
    ),
    "inspect": (
        "CO_ASYNC_GENERATOR",
        "CO_COROUTINE",
        "CO_GENERATOR",
        "CO_HAS_DOCSTRING",
        "CO_ITERABLE_COROUTINE",
        "CO_METHOD",
        "CO_NESTED",
        "CO_NEWLOCALS",
        "CO_NOFREE",
        "CO_OPTIMIZED",
        "CO_VARARGS",
        "CO_VARKEYWORDS",
        "TPFLAGS_IS_ABSTRACT",
    ),
    "io": (
        "DEFAULT_BUFFER_SIZE",
        "SEEK_CUR",
        "SEEK_END",
        "SEEK_SET",
    ),
    "logging": (
        "CRITICAL",
        "DEBUG",
        "ERROR",
        "FATAL",
        "INFO",
        "NOTSET",
        "WARN",
        "WARNING",
    ),
    "os": (
        "CLD_CONTINUED",
        "CLD_DUMPED",
        "CLD_EXITED",
        "CLD_KILLED",
        "CLD_STOPPED",
        "CLD_TRAPPED",
        "CLONE_FILES",
        "CLONE_FS",
        "CLONE_NEWCGROUP",
        "CLONE_NEWIPC",
        "CLONE_NEWNET",
        "CLONE_NEWNS",
        "CLONE_NEWPID",
        "CLONE_NEWTIME",
        "CLONE_NEWUSER",
        "CLONE_NEWUTS",
        "CLONE_SIGHAND",
        "CLONE_SYSVSEM",
        "CLONE_THREAD",
        "CLONE_VM",
        "EFD_CLOEXEC",
        "EFD_NONBLOCK",
        "EFD_SEMAPHORE",
        "EX_CANTCREAT",
        "EX_CONFIG",
        "EX_DATAERR",
        "EX_IOERR",
        "EX_NOHOST",
        "EX_NOINPUT",
        "EX_NOPERM",
        "EX_NOUSER",
        "EX_OK",
        "EX_OSERR",
        "EX_OSFILE",
        "EX_PROTOCOL",
        "EX_SOFTWARE",
        "EX_TEMPFAIL",
        "EX_UNAVAILABLE",
        "EX_USAGE",
        "F_LOCK",
        "F_OK",
        "F_TEST",
        "F_TLOCK",
        "F_ULOCK",
        "GRND_NONBLOCK",
        "GRND_RANDOM",
        "MFD_ALLOW_SEALING",
        "MFD_CLOEXEC",
        "MFD_HUGETLB",
        "MFD_HUGE_16GB",
        "MFD_HUGE_16MB",
        "MFD_HUGE_1GB",
        "MFD_HUGE_1MB",
        "MFD_HUGE_256MB",
        "MFD_HUGE_2GB",
        "MFD_HUGE_2MB",
        "MFD_HUGE_32MB",
        "MFD_HUGE_512KB",
        "MFD_HUGE_512MB",
        "MFD_HUGE_64KB",
        "MFD_HUGE_8MB",
        "MFD_HUGE_MASK",
        "MFD_HUGE_SHIFT",
        "NGROUPS_MAX",
        "O_ACCMODE",
        "O_APPEND",
        "O_ASYNC",
        "O_CLOEXEC",
        "O_CREAT",
        "O_DIRECT",
        "O_DIRECTORY",
        "O_DSYNC",
        "O_EXCL",
        "O_FSYNC",
        "O_LARGEFILE",
        "O_NDELAY",
        "O_NOATIME",
        "O_NOCTTY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
        "O_PATH",
        "O_RDONLY",
        "O_RDWR",
        "O_RSYNC",
        "O_SYNC",
        "O_TMPFILE",
        "O_TRUNC",
        "O_WRONLY",
        "POSIX_FADV_DONTNEED",
        "POSIX_FADV_NOREUSE",
        "POSIX_FADV_NORMAL",
        "POSIX_FADV_RANDOM",
        "POSIX_FADV_SEQUENTIAL",
        "POSIX_FADV_WILLNEED",
        "POSIX_SPAWN_CLOSE",
        "POSIX_SPAWN_DUP2",
        "POSIX_SPAWN_OPEN",
        "PRIO_PGRP",
        "PRIO_PROCESS",
        "PRIO_USER",
        "P_ALL",
        "P_NOWAIT",
        "P_NOWAITO",
        "P_PGID",
        "P_PID",
        "P_PIDFD",
        "P_WAIT",
        "RTLD_DEEPBIND",
        "RTLD_GLOBAL",
        "RTLD_LAZY",
        "RTLD_LOCAL",
        "RTLD_NODELETE",
        "RTLD_NOLOAD",
        "RTLD_NOW",
        "R_OK",
        "SCHED_BATCH",
        "SCHED_DEADLINE",
        "SCHED_FIFO",
        "SCHED_IDLE",
        "SCHED_NORMAL",
        "SCHED_OTHER",
        "SCHED_RESET_ON_FORK",
        "SCHED_RR",
        "SEEK_CUR",
        "SEEK_DATA",
        "SEEK_END",
        "SEEK_HOLE",
        "SEEK_SET",
        "SPLICE_F_MORE",
        "SPLICE_F_MOVE",
        "SPLICE_F_NONBLOCK",
        "ST_APPEND",
        "ST_MANDLOCK",
        "ST_NOATIME",
        "ST_NODEV",
        "ST_NODIRATIME",
        "ST_NOEXEC",
        "ST_NOSUID",
        "ST_RDONLY",
        "ST_RELATIME",
        "ST_SYNCHRONOUS",
        "ST_WRITE",
        "TFD_CLOEXEC",
        "TFD_NONBLOCK",
        "TFD_TIMER_ABSTIME",
        "TMP_MAX",
        "WCONTINUED",
        "WEXITED",
        "WNOHANG",
        "WNOWAIT",
        "WSTOPPED",
        "WUNTRACED",
        "W_OK",
        "XATTR_CREATE",
        "XATTR_REPLACE",
        "XATTR_SIZE_MAX",
        "X_OK",
    ),
    "pathlib": (
        "E2BIG",
        "EACCES",
        "EADDRINUSE",
        "EADDRNOTAVAIL",
        "EADV",
        "EAFNOSUPPORT",
        "EAGAIN",
        "EALREADY",
        "EBADE",
        "EBADF",
        "EBADFD",
        "EBADMSG",
        "EBADR",
        "EBADRQC",
        "EBADSLT",
        "EBFONT",
        "EBUSY",
        "ECANCELED",
        "ECHILD",
        "ECHRNG",
        "ECOMM",
        "ECONNABORTED",
        "ECONNREFUSED",
        "ECONNRESET",
        "EDEADLK",
        "EDEADLOCK",
        "EDESTADDRREQ",
        "EDOM",
        "EDOTDOT",
        "EDQUOT",
        "EEXIST",
        "EFAULT",
        "EFBIG",
        "EHOSTDOWN",
        "EHOSTUNREACH",
        "EHWPOISON",
        "EIDRM",
        "EILSEQ",
        "EINPROGRESS",
        "EINTR",
        "EINVAL",
        "EIO",
        "EISCONN",
        "EISDIR",
        "EISNAM",
        "EKEYEXPIRED",
        "EKEYREJECTED",
        "EKEYREVOKED",
        "EL2HLT",
        "EL2NSYNC",
        "EL3HLT",
        "EL3RST",
        "ELIBACC",
        "ELIBBAD",
        "ELIBEXEC",
        "ELIBMAX",
        "ELIBSCN",
        "ELNRNG",
        "ELOOP",
        "EMEDIUMTYPE",
        "EMFILE",
        "EMLINK",
        "EMSGSIZE",
        "EMULTIHOP",
        "ENAMETOOLONG",
        "ENAVAIL",
        "ENETDOWN",
        "ENETRESET",
        "ENETUNREACH",
        "ENFILE",
        "ENOANO",
        "ENOBUFS",
        "ENOCSI",
        "ENODATA",
        "ENODEV",
        "ENOENT",
        "ENOEXEC",
        "ENOKEY",
        "ENOLCK",
        "ENOLINK",
        "ENOMEDIUM",
        "ENOMEM",
        "ENOMSG",
        "ENONET",
        "ENOPKG",
        "ENOPROTOOPT",
        "ENOSPC",
        "ENOSR",
        "ENOSTR",
        "ENOSYS",
        "ENOTBLK",
        "ENOTCONN",
        "ENOTDIR",
        "ENOTEMPTY",
        "ENOTNAM",
        "ENOTRECOVERABLE",
        "ENOTSOCK",
        "ENOTSUP",
        "ENOTTY",
        "ENOTUNIQ",
        "ENXIO",
        "EOPNOTSUPP",
        "EOVERFLOW",
        "EOWNERDEAD",
        "EPERM",
        "EPFNOSUPPORT",
        "EPIPE",
        "EPROTO",
        "EPROTONOSUPPORT",
        "EPROTOTYPE",
        "ERANGE",
        "EREMCHG",
        "EREMOTE",
        "EREMOTEIO",
        "ERESTART",
        "ERFKILL",
        "EROFS",
        "ESHUTDOWN",
        "ESOCKTNOSUPPORT",
        "ESPIPE",
        "ESRCH",
        "ESRMNT",
        "ESTALE",
        "ESTRPIPE",
        "ETIME",
        "ETIMEDOUT",
        "ETOOMANYREFS",
        "ETXTBSY",
        "EUCLEAN",
        "EUNATCH",
        "EUSERS",
        "EWOULDBLOCK",
        "EXDEV",
        "EXFULL",
    ),
    "re": (
        "I",
        "IGNORECASE",
        "L",
        "LOCALE",
        "M",
        "MULTILINE",
        "NOFLAG",
        "S",
        "U",
        "UNICODE",
        "VERBOSE",
        "X",
    ),
    "shutil": ("COPY_BUFSIZE",),
    "socket": (
        "AF_ALG",
        "AF_APPLETALK",
        "AF_ASH",
        "AF_ATMPVC",
        "AF_ATMSVC",
        "AF_AX25",
        "AF_BRIDGE",
        "AF_CAN",
        "AF_DECnet",
        "AF_ECONET",
        "AF_IPX",
        "AF_IRDA",
        "AF_KEY",
        "AF_LLC",
        "AF_NETBEUI",
        "AF_NETLINK",
        "AF_NETROM",
        "AF_PACKET",
        "AF_PPPOX",
        "AF_RDS",
        "AF_ROSE",
        "AF_ROUTE",
        "AF_SECURITY",
        "AF_SNA",
        "AF_TIPC",
        "AF_UNIX",
        "AF_UNSPEC",
        "AF_VSOCK",
        "AF_WANPIPE",
        "AF_X25",
        "AI_ADDRCONFIG",
        "AI_ALL",
        "AI_CANONNAME",
        "AI_NUMERICHOST",
        "AI_NUMERICSERV",
        "AI_PASSIVE",
        "AI_V4MAPPED",
        "ALG_OP_DECRYPT",
        "ALG_OP_ENCRYPT",
        "ALG_OP_SIGN",
        "ALG_OP_VERIFY",
        "ALG_SET_AEAD_ASSOCLEN",
        "ALG_SET_AEAD_AUTHSIZE",
        "ALG_SET_IV",
        "ALG_SET_KEY",
        "ALG_SET_OP",
        "ALG_SET_PUBKEY",
        "CAN_BCM",
        "CAN_BCM_CAN_FD_FRAME",
        "CAN_BCM_RX_ANNOUNCE_RESUME",
        "CAN_BCM_RX_CHANGED",
        "CAN_BCM_RX_CHECK_DLC",
        "CAN_BCM_RX_DELETE",
        "CAN_BCM_RX_FILTER_ID",
        "CAN_BCM_RX_NO_AUTOTIMER",
        "CAN_BCM_RX_READ",
        "CAN_BCM_RX_RTR_FRAME",
        "CAN_BCM_RX_SETUP",
        "CAN_BCM_RX_STATUS",
        "CAN_BCM_RX_TIMEOUT",
        "CAN_BCM_SETTIMER",
        "CAN_BCM_STARTTIMER",
        "CAN_BCM_TX_ANNOUNCE",
        "CAN_BCM_TX_COUNTEVT",
        "CAN_BCM_TX_CP_CAN_ID",
        "CAN_BCM_TX_DELETE",
        "CAN_BCM_TX_EXPIRED",
        "CAN_BCM_TX_READ",
        "CAN_BCM_TX_RESET_MULTI_IDX",
        "CAN_BCM_TX_SEND",
        "CAN_BCM_TX_SETUP",
        "CAN_BCM_TX_STATUS",
        "CAN_EFF_FLAG",
        "CAN_EFF_MASK",
        "CAN_ERR_FLAG",
        "CAN_ERR_MASK",
        "CAN_ISOTP",
        "CAN_J1939",
        "CAN_RAW",
        "CAN_RAW_ERR_FILTER",
        "CAN_RAW_FD_FRAMES",
        "CAN_RAW_FILTER",
        "CAN_RAW_JOIN_FILTERS",
        "CAN_RAW_LOOPBACK",
        "CAN_RAW_RECV_OWN_MSGS",
        "CAN_RTR_FLAG",
        "CAN_SFF_MASK",
        "EAGAIN",
        "EAI_ADDRFAMILY",
        "EAI_AGAIN",
        "EAI_BADFLAGS",
        "EAI_FAIL",
        "EAI_FAMILY",
        "EAI_MEMORY",
        "EAI_NODATA",
        "EAI_NONAME",
        "EAI_OVERFLOW",
        "EAI_SERVICE",
        "EAI_SOCKTYPE",
        "EAI_SYSTEM",
        "EBADF",
        "ETHERTYPE_ARP",
        "ETHERTYPE_IP",
        "ETHERTYPE_IPV6",
        "ETHERTYPE_VLAN",
        "ETH_P_ALL",
        "EWOULDBLOCK",
        "INADDR_ALLHOSTS_GROUP",
        "INADDR_ANY",
        "INADDR_BROADCAST",
        "INADDR_LOOPBACK",
        "INADDR_MAX_LOCAL_GROUP",
        "INADDR_NONE",
        "INADDR_UNSPEC_GROUP",
        "IOCTL_VM_SOCKETS_GET_LOCAL_CID",
        "IPPORT_RESERVED",
        "IPPORT_USERRESERVED",
        "IPPROTO_AH",
        "IPPROTO_DSTOPTS",
        "IPPROTO_EGP",
        "IPPROTO_ESP",
        "IPPROTO_FRAGMENT",
        "IPPROTO_GRE",
        "IPPROTO_HOPOPTS",
        "IPPROTO_ICMP",
        "IPPROTO_ICMPV6",
        "IPPROTO_IDP",
        "IPPROTO_IGMP",
        "IPPROTO_IP",
        "IPPROTO_IPIP",
        "IPPROTO_IPV6",
        "IPPROTO_NONE",
        "IPPROTO_PIM",
        "IPPROTO_PUP",
        "IPPROTO_RAW",
        "IPPROTO_ROUTING",
        "IPPROTO_RSVP",
        "IPPROTO_SCTP",
        "IPPROTO_TCP",
        "IPPROTO_TP",
        "IPPROTO_UDP",
        "IPPROTO_UDPLITE",
        "IPV6_CHECKSUM",
        "IPV6_DONTFRAG",
        "IPV6_DSTOPTS",
        "IPV6_HOPLIMIT",
        "IPV6_HOPOPTS",
        "IPV6_JOIN_GROUP",
        "IPV6_LEAVE_GROUP",
        "IPV6_MULTICAST_HOPS",
        "IPV6_MULTICAST_IF",
        "IPV6_MULTICAST_LOOP",
        "IPV6_NEXTHOP",
        "IPV6_PATHMTU",
        "IPV6_PKTINFO",
        "IPV6_RECVDSTOPTS",
        "IPV6_RECVERR",
        "IPV6_RECVHOPLIMIT",
        "IPV6_RECVHOPOPTS",
        "IPV6_RECVPATHMTU",
        "IPV6_RECVPKTINFO",
        "IPV6_RECVRTHDR",
        "IPV6_RECVTCLASS",
        "IPV6_RTHDR",
        "IPV6_RTHDRDSTOPTS",
        "IPV6_RTHDR_TYPE_0",
        "IPV6_TCLASS",
        "IPV6_UNICAST_HOPS",
        "IPV6_V6ONLY",
        "IP_ADD_MEMBERSHIP",
        "IP_ADD_SOURCE_MEMBERSHIP",
        "IP_BIND_ADDRESS_NO_PORT",
        "IP_BLOCK_SOURCE",
        "IP_DEFAULT_MULTICAST_LOOP",
        "IP_DEFAULT_MULTICAST_TTL",
        "IP_DROP_MEMBERSHIP",
        "IP_DROP_SOURCE_MEMBERSHIP",
        "IP_FREEBIND",
        "IP_HDRINCL",
        "IP_MAX_MEMBERSHIPS",
        "IP_MULTICAST_IF",
        "IP_MULTICAST_LOOP",
        "IP_MULTICAST_TTL",
        "IP_OPTIONS",
        "IP_PKTINFO",
        "IP_RECVERR",
        "IP_RECVOPTS",
        "IP_RECVORIGDSTADDR",
        "IP_RECVRETOPTS",
        "IP_RECVTOS",
        "IP_RECVTTL",
        "IP_RETOPTS",
        "IP_TOS",
        "IP_TRANSPARENT",
        "IP_TTL",
        "IP_UNBLOCK_SOURCE",
        "J1939_EE_INFO_NONE",
        "J1939_EE_INFO_TX_ABORT",
        "J1939_FILTER_MAX",
        "J1939_IDLE_ADDR",
        "J1939_MAX_UNICAST_ADDR",
        "J1939_NLA_BYTES_ACKED",
        "J1939_NLA_PAD",
        "J1939_NO_ADDR",
        "J1939_NO_NAME",
        "J1939_NO_PGN",
        "J1939_PGN_ADDRESS_CLAIMED",
        "J1939_PGN_ADDRESS_COMMANDED",
        "J1939_PGN_MAX",
        "J1939_PGN_PDU1_MAX",
        "J1939_PGN_REQUEST",
        "MSG_CMSG_CLOEXEC",
        "MSG_CONFIRM",
        "MSG_CTRUNC",
        "MSG_DONTROUTE",
        "MSG_DONTWAIT",
        "MSG_EOR",
        "MSG_ERRQUEUE",
        "MSG_FASTOPEN",
        "MSG_MORE",
        "MSG_NOSIGNAL",
        "MSG_OOB",
        "MSG_PEEK",
        "MSG_TRUNC",
        "MSG_WAITALL",
        "NETLINK_CRYPTO",
        "NETLINK_DNRTMSG",
        "NETLINK_FIREWALL",
        "NETLINK_IP6_FW",
        "NETLINK_NFLOG",
        "NETLINK_ROUTE",
        "NETLINK_USERSOCK",
        "NETLINK_XFRM",
        "NI_DGRAM",
        "NI_IDN",
        "NI_MAXHOST",
        "NI_MAXSERV",
        "NI_NAMEREQD",
        "NI_NOFQDN",
        "NI_NUMERICHOST",
        "NI_NUMERICSERV",
        "PACKET_BROADCAST",
        "PACKET_FASTROUTE",
        "PACKET_HOST",
        "PACKET_LOOPBACK",
        "PACKET_MULTICAST",
        "PACKET_OTHERHOST",
        "PACKET_OUTGOING",
        "PF_CAN",
        "PF_PACKET",
        "PF_RDS",
        "SCM_CREDENTIALS",
        "SCM_J1939_DEST_ADDR",
        "SCM_J1939_DEST_NAME",
        "SCM_J1939_ERRQUEUE",
        "SCM_J1939_PRIO",
        "SCM_RIGHTS",
        "SHUT_RD",
        "SHUT_RDWR",
        "SHUT_WR",
        "SOCK_CLOEXEC",
        "SOCK_NONBLOCK",
        "SOCK_RAW",
        "SOCK_RDM",
        "SOCK_SEQPACKET",
        "SOL_ALG",
        "SOL_CAN_BASE",
        "SOL_CAN_RAW",
        "SOL_IP",
        "SOL_SOCKET",
        "SOL_TCP",
        "SOL_TIPC",
        "SOL_UDP",
        "SOMAXCONN",
        "SO_ACCEPTCONN",
        "SO_BINDTODEVICE",
        "SO_BINDTOIFINDEX",
        "SO_BROADCAST",
        "SO_DEBUG",
        "SO_DOMAIN",
        "SO_DONTROUTE",
        "SO_ERROR",
        "SO_INCOMING_CPU",
        "SO_J1939_ERRQUEUE",
        "SO_J1939_FILTER",
        "SO_J1939_PROMISC",
        "SO_J1939_SEND_PRIO",
        "SO_KEEPALIVE",
        "SO_LINGER",
        "SO_MARK",
        "SO_OOBINLINE",
        "SO_ORIGINAL_DST",
        "SO_PASSCRED",
        "SO_PASSSEC",
        "SO_PEERCRED",
        "SO_PEERSEC",
        "SO_PRIORITY",
        "SO_PROTOCOL",
        "SO_RCVBUF",
        "SO_RCVLOWAT",
        "SO_RCVTIMEO",
        "SO_REUSEADDR",
        "SO_REUSEPORT",
        "SO_SNDBUF",
        "SO_SNDLOWAT",
        "SO_SNDTIMEO",
        "SO_TYPE",
        "SO_VM_SOCKETS_BUFFER_MAX_SIZE",
        "SO_VM_SOCKETS_BUFFER_MIN_SIZE",
        "SO_VM_SOCKETS_BUFFER_SIZE",
        "TCP_CONGESTION",
        "TCP_CORK",
        "TCP_DEFER_ACCEPT",
        "TCP_FASTOPEN",
        "TCP_INFO",
        "TCP_KEEPCNT",
        "TCP_KEEPIDLE",
        "TCP_KEEPINTVL",
        "TCP_LINGER2",
        "TCP_MAXSEG",
        "TCP_MD5SIG",
        "TCP_NODELAY",
        "TCP_QUEUE_SEQ",
        "TCP_QUICKACK",
        "TCP_REPAIR",
        "TCP_REPAIR_OPTIONS",
        "TCP_REPAIR_QUEUE",
        "TCP_SYNCNT",
        "TCP_THIN_DUPACK",
        "TCP_THIN_LINEAR_TIMEOUTS",
        "TCP_TIMESTAMP",
        "TCP_USER_TIMEOUT",
        "TCP_WINDOW_CLAMP",
        "TIPC_ADDR_ID",
        "TIPC_ADDR_NAME",
        "TIPC_ADDR_NAMESEQ",
        "TIPC_CFG_SRV",
        "TIPC_CLUSTER_SCOPE",
        "TIPC_CONN_TIMEOUT",
        "TIPC_CRITICAL_IMPORTANCE",
        "TIPC_DEST_DROPPABLE",
        "TIPC_HIGH_IMPORTANCE",
        "TIPC_IMPORTANCE",
        "TIPC_LOW_IMPORTANCE",
        "TIPC_MEDIUM_IMPORTANCE",
        "TIPC_NODE_SCOPE",
        "TIPC_PUBLISHED",
        "TIPC_SRC_DROPPABLE",
        "TIPC_SUBSCR_TIMEOUT",
        "TIPC_SUB_CANCEL",
        "TIPC_SUB_PORTS",
        "TIPC_SUB_SERVICE",
        "TIPC_TOP_SRV",
        "TIPC_WAIT_FOREVER",
        "TIPC_WITHDRAWN",
        "TIPC_ZONE_SCOPE",
        "UDPLITE_RECV_CSCOV",
        "UDPLITE_SEND_CSCOV",
        "VMADDR_CID_ANY",
        "VMADDR_CID_HOST",
        "VMADDR_CID_LOCAL",
        "VMADDR_PORT_ANY",
        "VM_SOCKETS_INVALID_VERSION",
    ),
    "ssl": (
        "ALERT_DESCRIPTION_ACCESS_DENIED",
        "ALERT_DESCRIPTION_BAD_CERTIFICATE",
        "ALERT_DESCRIPTION_BAD_CERTIFICATE_HASH_VALUE",
        "ALERT_DESCRIPTION_BAD_CERTIFICATE_STATUS_RESPONSE",
        "ALERT_DESCRIPTION_BAD_RECORD_MAC",
        "ALERT_DESCRIPTION_CERTIFICATE_EXPIRED",
        "ALERT_DESCRIPTION_CERTIFICATE_REVOKED",
        "ALERT_DESCRIPTION_CERTIFICATE_UNKNOWN",
        "ALERT_DESCRIPTION_CERTIFICATE_UNOBTAINABLE",
        "ALERT_DESCRIPTION_CLOSE_NOTIFY",
        "ALERT_DESCRIPTION_DECODE_ERROR",
        "ALERT_DESCRIPTION_DECOMPRESSION_FAILURE",
        "ALERT_DESCRIPTION_DECRYPT_ERROR",
        "ALERT_DESCRIPTION_HANDSHAKE_FAILURE",
        "ALERT_DESCRIPTION_ILLEGAL_PARAMETER",
        "ALERT_DESCRIPTION_INSUFFICIENT_SECURITY",
        "ALERT_DESCRIPTION_INTERNAL_ERROR",
        "ALERT_DESCRIPTION_NO_RENEGOTIATION",
        "ALERT_DESCRIPTION_PROTOCOL_VERSION",
        "ALERT_DESCRIPTION_RECORD_OVERFLOW",
        "ALERT_DESCRIPTION_UNEXPECTED_MESSAGE",
        "ALERT_DESCRIPTION_UNKNOWN_CA",
        "ALERT_DESCRIPTION_UNKNOWN_PSK_IDENTITY",
        "ALERT_DESCRIPTION_UNRECOGNIZED_NAME",
        "ALERT_DESCRIPTION_UNSUPPORTED_CERTIFICATE",
        "ALERT_DESCRIPTION_UNSUPPORTED_EXTENSION",
        "ALERT_DESCRIPTION_USER_CANCELLED",
        "CERT_NONE",
        "CERT_OPTIONAL",
        "CERT_REQUIRED",
        "OPENSSL_VERSION_NUMBER",
        "OP_ALL",
        "OP_CIPHER_SERVER_PREFERENCE",
        "OP_ENABLE_KTLS",
        "OP_ENABLE_MIDDLEBOX_COMPAT",
        "OP_IGNORE_UNEXPECTED_EOF",
        "OP_LEGACY_SERVER_CONNECT",
        "OP_NO_COMPRESSION",
        "OP_NO_RENEGOTIATION",
        "OP_NO_SSLv2",
        "OP_NO_SSLv3",
        "OP_NO_TICKET",
        "OP_NO_TLSv1",
        "OP_NO_TLSv1_1",
        "OP_NO_TLSv1_2",
        "OP_NO_TLSv1_3",
        "OP_SINGLE_DH_USE",
        "OP_SINGLE_ECDH_USE",
        "PROTOCOL_SSLv23",
        "PROTOCOL_TLS",
        "PROTOCOL_TLS_CLIENT",
        "PROTOCOL_TLS_SERVER",
        "PROTOCOL_TLSv1",
        "PROTOCOL_TLSv1_1",
        "PROTOCOL_TLSv1_2",
        "SOCK_STREAM",
        "SOL_SOCKET",
        "SO_TYPE",
        "SSL_ERROR_EOF",
        "SSL_ERROR_INVALID_ERROR_CODE",
        "SSL_ERROR_SSL",
        "SSL_ERROR_SYSCALL",
        "SSL_ERROR_WANT_CONNECT",
        "SSL_ERROR_WANT_READ",
        "SSL_ERROR_WANT_WRITE",
        "SSL_ERROR_WANT_X509_LOOKUP",
        "SSL_ERROR_ZERO_RETURN",
        "VERIFY_ALLOW_PROXY_CERTS",
        "VERIFY_CRL_CHECK_CHAIN",
        "VERIFY_CRL_CHECK_LEAF",
        "VERIFY_DEFAULT",
        "VERIFY_X509_PARTIAL_CHAIN",
        "VERIFY_X509_STRICT",
        "VERIFY_X509_TRUSTED_FIRST",
    ),
    "stat": (
        "FILE_ATTRIBUTE_ARCHIVE",
        "FILE_ATTRIBUTE_COMPRESSED",
        "FILE_ATTRIBUTE_DEVICE",
        "FILE_ATTRIBUTE_DIRECTORY",
        "FILE_ATTRIBUTE_ENCRYPTED",
        "FILE_ATTRIBUTE_HIDDEN",
        "FILE_ATTRIBUTE_INTEGRITY_STREAM",
        "FILE_ATTRIBUTE_NORMAL",
        "FILE_ATTRIBUTE_NOT_CONTENT_INDEXED",
        "FILE_ATTRIBUTE_NO_SCRUB_DATA",
        "FILE_ATTRIBUTE_OFFLINE",
        "FILE_ATTRIBUTE_READONLY",
        "FILE_ATTRIBUTE_REPARSE_POINT",
        "FILE_ATTRIBUTE_SPARSE_FILE",
        "FILE_ATTRIBUTE_SYSTEM",
        "FILE_ATTRIBUTE_TEMPORARY",
        "FILE_ATTRIBUTE_VIRTUAL",
        "SF_APPEND",
        "SF_ARCHIVED",
        "SF_DATALESS",
        "SF_FIRMLINK",
        "SF_IMMUTABLE",
        "SF_NOUNLINK",
        "SF_RESTRICTED",
        "SF_SETTABLE",
        "SF_SNAPSHOT",
        "ST_ATIME",
        "ST_CTIME",
        "ST_DEV",
        "ST_GID",
        "ST_INO",
        "ST_MODE",
        "ST_MTIME",
        "ST_NLINK",
        "ST_SIZE",
        "ST_UID",
        "S_ENFMT",
        "S_IEXEC",
        "S_IFBLK",
        "S_IFCHR",
        "S_IFDOOR",
        "S_IFIFO",
        "S_IFPORT",
        "S_IFSOCK",
        "S_IFWHT",
        "S_IREAD",
        "S_IRGRP",
        "S_IROTH",
        "S_IRWXG",
        "S_IRWXO",
        "S_IRWXU",
        "S_ISGID",
        "S_ISUID",
        "S_ISVTX",
        "S_IWGRP",
        "S_IWOTH",
        "S_IWRITE",
        "S_IWUSR",
        "S_IXGRP",
        "S_IXOTH",
        "S_IXUSR",
        "UF_APPEND",
        "UF_COMPRESSED",
        "UF_DATAVAULT",
        "UF_HIDDEN",
        "UF_IMMUTABLE",
        "UF_NODUMP",
        "UF_NOUNLINK",
        "UF_OPAQUE",
        "UF_SETTABLE",
        "UF_TRACKED",
    ),
    "subprocess": (
        "PIPE",
        "STDOUT",
    ),
    "sys": (
        "api_version",
        "hexversion",
        "maxunicode",
    ),
    "tempfile": ("TMP_MAX",),
    "time": (
        "CLOCK_BOOTTIME",
        "CLOCK_MONOTONIC",
        "CLOCK_MONOTONIC_RAW",
        "CLOCK_PROCESS_CPUTIME_ID",
        "CLOCK_REALTIME",
        "CLOCK_THREAD_CPUTIME_ID",
        "altzone",
        "daylight",
        "timezone",
    ),
}


# Harness str probes on modules the 044 snapshot already marks lowered.
# The fold is len * 1000 + ord(first), or 0 when the string is empty.
# Values are read from this interpreter. Code points above Latin-1 are omitted.
_STR_SURFACE: dict[str, tuple[str, ...]] = {
    "asyncio": (
        "FIRST_COMPLETED",
        "FIRST_EXCEPTION",
    ),
    "inspect": (
        "AGEN_CREATED",
        "AGEN_RUNNING",
        "AGEN_SUSPENDED",
        "CORO_CLOSED",
        "CORO_CREATED",
        "CORO_RUNNING",
        "CORO_SUSPENDED",
        "GEN_CLOSED",
        "GEN_CREATED",
        "GEN_RUNNING",
        "GEN_SUSPENDED",
    ),
    "os": (
        "curdir",
        "defpath",
        "devnull",
        "extsep",
        "linesep",
        "name",
        "pardir",
        "pathsep",
        "sep",
    ),
    "posixpath": (
        "curdir",
        "defpath",
        "devnull",
        "extsep",
        "pardir",
        "pathsep",
        "sep",
    ),
    "ssl": (
        "PEM_FOOTER",
        "PEM_HEADER",
    ),
    "sys": (
        "abiflags",
        "base_exec_prefix",
        "base_prefix",
        "byteorder",
        "copyright",
        "exec_prefix",
        "float_repr_style",
        "platlibdir",
        "prefix",
    ),
    "tempfile": ("template",),
}


# Harness bool probes on modules the 045 snapshot already marks lowered.
# The probe is `if module.NAME: return 1` else `return 0`.
_BOOL_SURFACE: dict[str, tuple[str, ...]] = {
    "logging": (
        "logAsyncioTasks",
        "logMultiprocessing",
        "logProcesses",
        "logThreads",
        "raiseExceptions",
    ),
    "os": ("supports_bytes_environ",),
    "posixpath": ("supports_unicode_filenames",),
    "socket": ("has_ipv6",),
    "ssl": (
        "HAS_ALPN",
        "HAS_ECDH",
        "HAS_NEVER_CHECK_COMMON_NAME",
        "HAS_NPN",
        "HAS_PHA",
        "HAS_PSK",
        "HAS_SSLv2",
        "HAS_SSLv3",
        "HAS_TLSv1",
        "HAS_TLSv1_1",
        "HAS_TLSv1_2",
        "HAS_TLSv1_3",
    ),
    "sys": ("dont_write_bytecode",),
}


# Synthetic classes installed when their module is imported. TextIO and BytesIO
# are installed for every module because builtin open returns them.
ALWAYS_CLASSES: tuple[str, ...] = ("TextIO", "BytesIO")


MODULE_CLASSES: dict[str, tuple[str, ...]] = {
    "datetime": ("Date", "DateTime", "Time", "TimeDelta"),
    "time": ("StructTime",),
    "pathlib": ("Path", "Stat"),
    "os": ("Stat", "TerminalSize"),
    "shutil": ("TerminalSize",),
    "subprocess": ("CompletedProcess", "CompletedProcessText"),
    "collections": ("Deque",),
    "hmac": ("Hmac",),
    "importlib.resources": ("Path",),
    "csv": ("CsvWriter",),
    "argparse": ("ArgParser", "Namespace"),
    "logging": ("Logger",),
    "urllib.request": ("HttpRequest", "HttpResponse"),
    "urllib.error": ("HTTPError", "URLError"),
    "queue": ("Queue", "Empty", "Full"),
    "asyncio": ("CancelledError",),
}

CLASS_FIELDS: dict[str, list[tuple[str, T]]] = {
    "Date": _plain_fields("year", "month", "day"),
    "DateTime": _plain_fields("year", "month", "day", "hour", "minute", "second", "microsecond"),
    "Time": _plain_fields("hour", "minute", "second", "microsecond"),
    "TimeDelta": _plain_fields("days", "seconds", "microseconds"),
    "StructTime": _plain_fields(
        "tm_year", "tm_mon", "tm_mday", "tm_hour", "tm_min", "tm_sec", "tm_wday", "tm_yday", "tm_isdst"
    ),
    "Path": [("_path", _S)],
    "Stat": [
        ("st_mode", _I),
        ("st_size", _I),
        ("st_mtime", _F),
    ],
    "Deque": [],
    "TextIO": [],
    "BytesIO": [],
    "Queue": [],
    "Empty": [],
    "Full": [],
    "CancelledError": [],
    "TerminalSize": [
        ("columns", _I),
        ("lines", _I),
    ],
    "CompletedProcess": [
        ("returncode", _I),
        ("stdout", _BY),
        ("stderr", _BY),
    ],
    # The text-mode record: the same three fields, with the captured
    # streams decoded to str (issue #680).
    "CompletedProcessText": [
        ("returncode", _I),
        ("stdout", _S),
        ("stderr", _S),
    ],
    "Hmac": [
        ("_key", _BY),
        ("_msg", _BY),
        ("_alg", _I),
    ],
    # The parser holds the encoded spec rows; the namespace fields are
    # declared as the options are registered, so they start empty here.
    "ArgParser": [("_specs", _STR_LIST)],
    "Namespace": [],
    # The logger carries the name getLogger recorded.
    "Logger": [("_name", _S)],
    # The request record carries what urlopen sends; the response carries what
    # came back. The headers ride as the wire block ("Name: value\n" rows).
    "HttpRequest": [
        ("url", _S),
        ("method", _S),
        ("body", _BY),
        ("headers", _S),
        ("timeout", _F),
    ],
    "HttpResponse": [
        ("status", _I),
        ("headers", _S),
        ("body", _BY),
    ],
    # HTTPError keeps the response the server sent: the body stays readable
    # through read(), the status is the .code CPython puts on the error.
    "HTTPError": [
        ("status", _I),
        ("headers", _S),
        ("body", _BY),
    ],
    "URLError": [],
}

# Classes whose fields grow while the statements lower. They are emitted
# after the functions, not with the other synthetic classes.
DEFERRED_CLASSES = frozenset({"Namespace"})

# Synthetic classes that lower as exceptions. The runtime registers each at
# module init, so `except` matches through the builtin parent chain.
SYNTHETIC_EXCEPTIONS: dict[str, int] = {"HTTPError": 24, "URLError": 24}

# Reserved-code exceptions the import binds as classes: `except Empty:` and
# `except Full:` match the runtime's reserved codes through the parent walk,
# so user codes never shift. The value is the CPython builtin parent's code.
RESERVED_CODE_EXCEPTIONS: dict[str, int] = {
    "Empty": RESERVED_EXCEPTIONS["Empty"],
    "Full": RESERVED_EXCEPTIONS["Full"],
    "CancelledError": RESERVED_EXCEPTIONS["CancelledError"],
}

# i64 field packs compared lexicographically. Path compares its string.
LEX_CLASSES = frozenset({"Date", "DateTime", "Time", "TimeDelta"})
COMPARE_CLASSES = LEX_CLASSES | {"Path"}

# Class constants: `date.min` and friends read a runtime record instead of a
# baked literal, so the values stay right when the class layout changes.
CLASS_CONSTANTS: dict[tuple[str, str], tuple[T, str]] = {
    ("datetime", "date.min"): (_DATE, "ps_date_min"),
    ("datetime", "date.max"): (_DATE, "ps_date_max"),
    ("datetime", "datetime.min"): (_DT, "ps_dt_min"),
    ("datetime", "datetime.max"): (_DT, "ps_dt_max"),
}

# The threading records are runtime pointers. A module-level binding keeps
# its identity through the global slot, not through a re-emitted call.
THREADING_CLASSES: frozenset[str] = frozenset({"Thread", "Event", "Condition", "Semaphore", "Lock"})

# The queue records are runtime pointers, one shape for Queue, LifoQueue and
# SimpleQueue: the element type rides on the class, like Deque's does.
QUEUE_CLASSES: frozenset[str] = frozenset({"Queue"})

# The asyncio primitives are runtime pointers too, one shape each: Lock,
# Event, Semaphore and Queue. The wait is cooperative, so the records hold a
# waiter list of tasks rather than a pthread condvar.
ASYNC_CLASSES: frozenset[str] = frozenset({"AsyncLock", "AsyncEvent", "AsyncSemaphore", "AsyncQueue"})

# The records that live in a global slot: a runtime pointer whose identity
# has to hold across reads, the threading records and the queue records.
PTR_CLASSES: frozenset[str] = THREADING_CLASSES | QUEUE_CLASSES | ASYNC_CLASSES | {"ArgParser"}

TYPE_ALIAS: dict[tuple[str, str], str] = {
    ("datetime", "date"): "Date",
    ("datetime", "datetime"): "DateTime",
    ("datetime", "time"): "Time",
    ("datetime", "timedelta"): "TimeDelta",
    ("pathlib", "Path"): "Path",
    ("collections", "deque"): "Deque",
    ("io", "StringIO"): "TextIO",
    ("io", "BytesIO"): "BytesIO",
    ("time", "struct_time"): "StructTime",
    ("os", "stat_result"): "Stat",
    ("csv", "_writer"): "CsvWriter",
    ("argparse", "ArgumentParser"): "ArgParser",
    ("argparse", "Namespace"): "Namespace",
    ("logging", "Logger"): "Logger",
    ("re", "Pattern"): "Pattern",
    ("re", "Match"): "Match",
    ("threading", "Lock"): "Lock",
    ("threading", "RLock"): "Lock",
    ("os", "terminal_size"): "TerminalSize",
    ("subprocess", "CompletedProcess"): "CompletedProcess",
    ("threading", "Thread"): "Thread",
    ("threading", "Event"): "Event",
    ("threading", "Condition"): "Condition",
    ("threading", "Semaphore"): "Semaphore",
    ("queue", "Queue"): "Queue",
    ("queue", "LifoQueue"): "Queue",
    ("queue", "SimpleQueue"): "Queue",
    ("queue", "Empty"): "Empty",
    ("queue", "Full"): "Full",
    ("urllib.parse", "SplitResult"): "SplitResult",
    ("urllib.request", "HTTPResponse"): "HttpResponse",
    ("urllib.request", "HttpResponse"): "HttpResponse",
    ("urllib.request", "HttpRequest"): "HttpRequest",
    ("urllib.request", "Request"): "HttpRequest",
    ("urllib.error", "HTTPError"): "HTTPError",
    ("urllib.error", "URLError"): "URLError",
    ("asyncio", "Task"): "Task",
    ("asyncio", "CancelledError"): "CancelledError",
    ("asyncio", "Lock"): "AsyncLock",
    ("asyncio", "Event"): "AsyncEvent",
    ("asyncio", "Semaphore"): "AsyncSemaphore",
    ("asyncio", "Queue"): "AsyncQueue",
}

# Instance methods. `elem` means the deque's element type.
CLASS_METHODS: dict[str, dict[str, Sig]] = {
    "Date": {
        "isoformat": sig([], _S, "ps_date_iso"),
        "toordinal": sig([], _I, "ps_date_ord"),
        "weekday": sig([], _I, "ps_date_weekday"),
    },
    "DateTime": {
        "isoformat": sig([], _S, "ps_dt_iso"),
        "date": sig([], _DATE, "ps_dt_date"),
        "time": sig([], _TOD, "ps_dt_time"),
        "timestamp": sig([], _F, "ps_dt_timestamp"),
    },
    "Time": {"isoformat": sig([], _S, "ps_tod_iso")},
    "TimeDelta": {"total_seconds": sig([], _F, "ps_delta_total")},
    "Path": {
        "as_posix": sig([], _S, "ps_path_posix"),
        "exists": sig([], _B, "ps_path_exists_p"),
        "is_file": sig([], _B, "ps_path_isfile_p"),
        "is_dir": sig([], _B, "ps_path_isdir_p"),
        "is_absolute": sig([], _B, "ps_path_isabs_p"),
        "read_text": sig([], _S, "ps_path_read_text"),
        "read_bytes": sig([], _BY, "ps_path_read_bytes"),
        "write_text": sig([_S], _I, "ps_path_write_text"),
        "write_bytes": sig([_BY], _I, "ps_path_write_bytes"),
        "joinpath": sig([_S], _PATH, "ps_path_joinpath"),
        "with_name": sig([_S], _PATH, "ps_path_with_name"),
        "resolve": sig([], _PATH, "ps_path_resolve"),
        "unlink": sig([], _N, "ps_path_unlink"),
        "touch": sig([], _N, "ps_path_touch"),
        "rename": sig([_S], _PATH, "ps_path_rename"),
        "stat": sig([], _STAT, "ps_path_stat"),
        "expanduser": sig([], _PATH, "ps_path_expanduser_p"),
        # iterdir/glob return the materialized list[Path]; lowering wraps it
        # in the R24 iterator record. mkdir is special-cased for keywords.
        "iterdir": sig([], t_list(_PATH), "ps_path_iterdir"),
        "glob": sig([_S], t_list(_PATH), "ps_path_glob"),
        "mkdir": sig([], _N, "ps_path_mkdir"),
    },
    "TextIO": {
        "read": sig([], _S, "ps_io_read"),
        "readline": sig([], _S, "ps_io_readline"),
        "write": sig([_S], _I, "ps_io_write"),
        "close": sig([], _N, "ps_io_close"),
        "getvalue": sig([], _S, "ps_io_value"),
    },
    "BytesIO": {
        "read": sig([], _BY, "ps_io_read"),
        "readline": sig([], _BY, "ps_io_readline"),
        "write": sig([_BY], _I, "ps_io_write"),
        "close": sig([], _N, "ps_io_close"),
        "getvalue": sig([], _BY, "ps_io_value"),
    },
    "Deque": {
        "append": {"params": ["elem"], "return": {"kind": "none"}, "symbol": "ps_deque_append"},
        "appendleft": {"params": ["elem"], "return": {"kind": "none"}, "symbol": "ps_deque_appendleft"},
        "pop": {"params": [], "return": "elem", "symbol": "ps_deque_pop"},
        "popleft": {"params": [], "return": "elem", "symbol": "ps_deque_popleft"},
    },
    "Hmac": {"digest": sig([], _BY, "ps_hmac_finish")},
    "Logger": {
        "debug": sig([_S], _N, "ps_log_debug"),
        "info": sig([_S], _N, "ps_log_info"),
        "warning": sig([_S], _N, "ps_log_warning"),
        "error": sig([_S], _N, "ps_log_error"),
        "critical": sig([_S], _N, "ps_log_critical"),
        "exception": sig([_S], _N, "ps_log_exception"),
    },
    "CsvWriter": {
        "writerow": sig([_LS], _N, "ps_csv_writerow"),
        "writerows": sig([_LLS], _N, "ps_csv_writerows"),
    },
    "HttpResponse": {
        "getcode": sig([], _I, "ps_http_getcode"),
        "info": sig([], _S, "ps_http_info"),
        "read": sig([], _BY, "ps_http_read"),
    },
    "HttpRequest": {},
}

PATH_ATTRS: dict[str, tuple[T, str]] = {
    "name": (_S, "ps_path_name"),
    "suffix": (_S, "ps_path_suffix"),
    "stem": (_S, "ps_path_stem"),
    "parent": (_PATH, "ps_path_parent"),
}

REST_FIXED: dict[str, dict[str, Sig]] = {
    "datetime": {
        "date.today": sig([], _DATE, "ps_date_today"),
        "date.fromisoformat": sig([_S], _DATE, "ps_date_fromiso"),
        "date.fromordinal": sig([_I], _DATE, "ps_date_fromord"),
        "date.fromtimestamp": sig([_F], _DATE, "ps_date_fromtimestamp"),
        "datetime.now": sig([], _DT, "ps_dt_now"),
        "datetime.fromisoformat": sig([_S], _DT, "ps_dt_fromiso"),
        "datetime.fromtimestamp": sig([_F], _DT, "ps_dt_fromtimestamp"),
        "datetime.strptime": sig([_S, _S], _DT, "ps_dt_strptime_entry"),
        "datetime.combine": sig([_DATE, _TOD], _DT, "ps_dt_combine"),
    },
    "time": {
        "time": sig([], _F, "ps_time_time"),
        "monotonic": sig([], _F, "ps_time_monotonic"),
        "perf_counter": sig([], _F, "ps_time_perf_counter"),
        "strftime": sig([_S, _STM], _S, "ps_time_strftime"),
        "mktime": sig([_STM], _F, "ps_time_mktime"),
    },
    "pathlib": {
        "Path.cwd": sig([], _PATH, "ps_path_cwd"),
        "Path.home": sig([], _PATH, "ps_path_home"),
    },
    "os": {
        "getcwd": sig([], _S, "ps_os_getcwd"),
        "listdir": sig([_S], _LS, "ps_os_listdir"),
        "getenv": sig([_S], _S, "ps_os_getenv"),
        "remove": sig([_S], _N, "ps_os_remove"),
        "rename": sig([_S, _S], _N, "ps_os_rename"),
        "makedirs": sig([_S], _N, "ps_os_makedirs"),
        "stat": sig([_S], _STAT, "ps_os_stat"),
        "access": sig([_S, _I], _B, "ps_os_access"),
        "getpid": sig([], _I, "ps_os_getpid"),
        "chdir": sig([_S], _N, "ps_os_chdir"),
        "chmod": sig([_S, _I], _N, "ps_os_chmod"),
        "symlink": sig([_S, _S], _N, "ps_os_symlink"),
        "readlink": sig([_S], _S, "ps_os_readlink"),
        "mkdir": sig([_S], _N, "ps_os_mkdir"),
        "replace": sig([_S, _S], _N, "ps_os_replace"),
        "unlink": sig([_S], _N, "ps_os_remove"),
        "close": sig([_I], _N, "ps_os_close"),
        "write": sig([_I, _BY], _I, "ps_os_write"),
        "fstat": sig([_I], _STAT, "ps_os_fstat"),
        "lstat": sig([_S], _STAT, "ps_os_lstat"),
        "utime": sig([_S], _N, "ps_os_utime"),
    },
    "posixpath": {
        "basename": sig([_S], _S, "ps_path_base"),
        "dirname": sig([_S], _S, "ps_path_dir"),
        "split": sig([_S], _LS, "ps_path_split"),
        "splitext": sig([_S], _LS, "ps_path_splitext"),
        "normpath": sig([_S], _S, "ps_path_norm"),
        "isabs": sig([_S], _B, "ps_path_isabs"),
        "abspath": sig([_S], _S, "ps_path_abs"),
        "exists": sig([_S], _B, "ps_path_exists"),
        "isfile": sig([_S], _B, "ps_path_isfile"),
        "isdir": sig([_S], _B, "ps_path_isdir"),
        "expanduser": sig([_S], _S, "ps_path_expanduser"),
        "expandvars": sig([_S], _S, "ps_path_expandvars"),
        "realpath": sig([_S], _S, "ps_path_realpath"),
        "getmtime": sig([_S], _F, "ps_path_getmtime"),
        "getsize": sig([_S], _I, "ps_path_getsize"),
        "getatime": sig([_S], _F, "ps_path_getatime"),
        "islink": sig([_S], _B, "ps_path_islink"),
        "splitdrive": sig([_S], _LS, "ps_path_splitdrive"),
    },
    "stat": {
        "S_ISREG": sig([_I], _B, "ps_stat_isreg"),
        "S_ISDIR": sig([_I], _B, "ps_stat_isdir"),
        "S_ISLNK": sig([_I], _B, "ps_stat_islnk"),
    },
    "fnmatch": {
        "fnmatch": sig([_S, _S], _B, "ps_fnmatch"),
        "filter": sig([_LS, _S], _LS, "ps_fnfilter"),
    },
    "warnings": {"warn": sig([_S], _N, "ps_warn")},
    "inspect": {"cleandoc": sig([_S], _S, "ps_island_cleandoc")},
    "logging": {
        "getLogger": sig([_S], t_class("Logger"), "ps_log_new"),
        "debug": sig([_S], _N, "ps_island_log_debug"),
        "info": sig([_S], _N, "ps_island_log_info"),
        "warning": sig([_S], _N, "ps_island_log_warning"),
        "error": sig([_S], _N, "ps_island_log_error"),
        "critical": sig([_S], _N, "ps_island_log_critical"),
    },
    "socket": {"gethostname": sig([], _S, "ps_island_hostname")},
    "threading": {"active_count": sig([], _I, "ps_thread_active_count")},
    "subprocess": {"check_output": sig([t_list(_S)], _BY, "ps_island_check_output")},
    "sys": {
        "stdout.write": sig([_S], _I, "ps_stdout_write"),
        "stderr.write": sig([_S], _I, "ps_stderr_write"),
    },
    "unicodedata": {
        "category": sig([_S], _S, "ps_unicode_category"),
        "combining": sig([_S], _I, "ps_unicode_combining"),
        "normalize": sig([_S, _S], _S, "ps_unicode_normalize"),
        "is_normalized": sig([_S, _S], _B, "ps_unicode_is_normalized"),
        "bidirectional": sig([_S], _S, "ps_unicode_bidirectional"),
        "east_asian_width": sig([_S], _S, "ps_unicode_east_asian_width"),
        "mirrored": sig([_S], _I, "ps_unicode_mirrored"),
        "decimal": sig([_S], _I, "ps_unicode_decimal"),
        "digit": sig([_S], _I, "ps_unicode_digit"),
        "numeric": sig([_S], _F, "ps_unicode_numeric"),
        "decomposition": sig([_S], _S, "ps_unicode_decomposition"),
        "lookup": sig([_S], _S, "ps_unicode_lookup"),
    },
    "tempfile": {
        "gettempdir": sig([], _S, "ps_temp_getdir"),
        "gettempprefix": sig([], _S, "ps_temp_prefix"),
        "mkdtemp": sig([], _S, "ps_temp_mkdtemp"),
    },
    "shutil": {
        "copyfile": sig([_S, _S], _S, "ps_shutil_copyfile"),
        "rmtree": sig([_S], _N, "ps_shutil_rmtree"),
        # `which` keeps its row for the call-table inventory (the probe
        # census counts it), but the _EARLY handler in restlower takes the
        # call first: the return is the tagged `str | None`, not the fixed
        # str this row would hand the fixed-call path.
        "which": sig([_S], _S, "ps_shutil_which"),
        "copy": sig([_S, _S], _S, "ps_shutil_copy"),
        "copy2": sig([_S, _S], _S, "ps_shutil_copy2"),
        "copytree": sig([_S, _S], _S, "ps_shutil_copytree"),
        "move": sig([_S, _S], _S, "ps_shutil_move"),
        "get_terminal_size": sig([], t_class("TerminalSize"), "ps_shutil_get_terminal_size"),
    },
    "textwrap": {
        "dedent": sig([_S], _S, "ps_wrap_dedent"),
        "indent": sig([_S, _S], _S, "ps_wrap_indent"),
    },
    "platform": {
        "system": sig([], _S, "ps_platform_system"),
        "machine": sig([], _S, "ps_platform_machine"),
        "python_version": sig([], _S, "ps_platform_python_version"),
        "platform": sig([], _S, "ps_platform_platform"),
    },
    "calendar": {
        "isleap": sig([_I], _B, "ps_calendar_isleap"),
        "leapdays": sig([_I, _I], _I, "ps_calendar_leapdays"),
        "weekday": sig([_I, _I, _I], _I, "ps_calendar_weekday"),
    },
    "zipfile": {
        "is_zipfile": sig([_S], _B, "ps_zipfile_is_zipfile"),
    },
    "tarfile": {
        "is_tarfile": sig([_S], _B, "ps_tarfile_is_tarfile"),
    },
}

# Callable, but only in a particular statement shape.
FOR_ONLY = frozenset({("itertools", "repeat")})
WITH_ONLY = frozenset({("contextlib", "suppress"), ("contextlib", "nullcontext"), ("importlib.resources", "as_file")})

CALL_FENCES: dict[tuple[str, str], str] = {
    ("importlib", "import_module"): "import_module returns a module",
    ("sys", "exit"): "",
    ("functools", "partial"): "functools.partial is not lowered",
    ("functools", "wraps"): "decorators are not lowered",
    ("functools", "lru_cache"): "decorators are not lowered",
    ("functools", "cmp_to_key"): "functools.cmp_to_key is not lowered",
    ("itertools", "count"): "infinite iterators are not lowered",
    ("itertools", "cycle"): "infinite iterators are not lowered",
    ("operator", "itemgetter"): "operator.itemgetter is not a value",
    ("operator", "attrgetter"): "operator.attrgetter is not a value",
    ("operator", "methodcaller"): "operator.methodcaller is not a value",
    ("contextlib", "contextmanager"): "contextmanager is only lowered as a decorator",
    ("threading", "current_thread"): "threading.current_thread is only lowered for .name",
}

OPERATORS = frozenset(
    {"add", "sub", "mul", "floordiv", "truediv", "mod", "neg", "eq", "ne", "lt", "le", "gt", "ge", "concat"}
)

CTYPES = frozenset({"c_char", "c_int", "c_long", "c_int64", "c_size_t", "c_void_p"})

REST_MODULES = frozenset(
    {
        "datetime",
        "time",
        "collections",
        "functools",
        "itertools",
        "operator",
        "contextlib",
        "os",
        "posixpath",
        "pathlib",
        "stat",
        "fnmatch",
        "warnings",
        "io",
        "sys",
        "importlib",
        "inspect",
        "threading",
        "logging",
        "asyncio",
        "subprocess",
        "socket",
        "ssl",
        "ctypes",
        "string",
        "bisect",
        "errno",
        "importlib.resources",
        "hmac",
        "unicodedata",
        "tempfile",
        "atexit",
        "shutil",
        "random",
        "csv",
        "textwrap",
        "copy",
        "argparse",
        "tomllib",
        "dotenv",
        "yaml",
        "urllib.request",
        "jwt",
        "orjson",
        "queue",
        "platform",
        "calendar",
        "zipfile",
        "tarfile",
        "uuid",
        "binascii",
        "shlex",
        "secrets",
    }
)


def _ir_const(value: Any) -> tuple[Any, dict[str, Any]] | None:
    """`(value, dict type row)`: the constants feed the IR, so the record
    of the typing-hygiene slice converts here, at the boundary."""

    if isinstance(value, bool):
        return value, type_to_dict(_B)
    if isinstance(value, int):
        return str(int(value)), type_to_dict(_I)
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            return None
        return value, type_to_dict(_S)
    if isinstance(value, float):
        return repr(value), type_to_dict(_F)
    return None


def _target_version() -> str:
    """The CPython version the emitted program claims to be ('3.14.9').

    The declared target lives in `src/pyscriptc/target.json`; the tool host
    may be 3.11 through 3.15 and does not change the program's identity.
    """
    from pyscriptc.target import TARGET_CPYTHON

    return TARGET_CPYTHON


def _target_sys_version() -> str:
    """The `sys.version` string the emitted program reports.

    CPython's own string carries the build date and the compiler that built
    the interpreter (`3.14.9 (main, Oct 21 2026, 17:47:42) [Clang 23.1.3 ]`),
    which is a property of the interpreter build, not of the program. The
    emitted program reports the target version with the same shape, so a
    3.11 tool host and a 3.14 tool host bake the same constant and the
    program matches the 3.14 oracle. The build stamp is not reproducible
    outside the interpreter that built it, so it is dropped.
    """
    version = _target_version()
    if sys.version.startswith(version):
        return sys.version
    return version


def _target_sys_executable() -> str:
    """The `sys.executable` the emitted program reports: the compiler host's.

    Deliberately not target-pinned. The canonical form already classifies an
    environment-derived absolute path as incidental (`<PATH>` scrub,
    decision 2026-10-16), and the one place a program's own `sys.executable`
    must agree with a real interpreter is the island (#688, #690), where the
    runtime sets it from the running image, never from this constant. A
    target-pinned path would dangle in the common case (a 3.11 host with no
    3.14 interpreter installed) and is not an honest answer either.
    """
    return sys.executable


def _load_constants() -> tuple[dict[tuple[str, str], tuple[Any, dict[str, Any]]], dict[str, int]]:
    import asyncio
    import ctypes
    import errno
    import importlib
    import inspect
    import logging
    import re
    import string
    import subprocess
    import threading
    import unicodedata
    import warnings

    raw: dict[tuple[str, str], Any] = {
        ("sys", "platform"): sys.platform,
        ("sys", "version"): _target_sys_version(),
        ("sys", "executable"): _target_sys_executable(),
        ("sys", "maxsize"): sys.maxsize,
        ("socket", "AF_INET"): int(socket.AF_INET),
        ("socket", "AF_INET6"): int(socket.AF_INET6),
        ("socket", "SOCK_STREAM"): int(socket.SOCK_STREAM),
        ("socket", "SOCK_DGRAM"): int(socket.SOCK_DGRAM),
        ("ssl", "OPENSSL_VERSION"): ssl.OPENSSL_VERSION,
        ("ssl", "HAS_SNI"): bool(ssl.HAS_SNI),
        ("stat", "S_IFREG"): int(stat.S_IFREG),
        ("stat", "S_IFDIR"): int(stat.S_IFDIR),
        ("stat", "S_IFLNK"): int(stat.S_IFLNK),
        ("stat", "S_IRUSR"): int(stat.S_IRUSR),
        ("string", "ascii_lowercase"): string.ascii_lowercase,
        ("string", "ascii_uppercase"): string.ascii_uppercase,
        ("string", "ascii_letters"): string.ascii_letters,
        ("string", "digits"): string.digits,
        ("string", "hexdigits"): string.hexdigits,
        ("string", "octdigits"): string.octdigits,
        ("string", "punctuation"): string.punctuation,
        ("string", "whitespace"): string.whitespace,
        ("string", "printable"): string.printable,
        ("unicodedata", "unidata_version"): unicodedata.unidata_version,
        ("re", "A"): int(re.A),
        ("re", "ASCII"): int(re.ASCII),
        ("re", "DEBUG"): int(re.DEBUG),
        ("re", "DOTALL"): int(re.DOTALL),
        ("warnings", "defaultaction"): warnings.defaultaction,
        ("threading", "TIMEOUT_MAX"): threading.TIMEOUT_MAX,
        ("logging", "BASIC_FORMAT"): logging.BASIC_FORMAT,
        ("asyncio", "ALL_COMPLETED"): asyncio.ALL_COMPLETED,
        ("subprocess", "DEVNULL"): int(subprocess.DEVNULL),
    }
    agen_closed = _host_attr(inspect, "AGEN_CLOSED")
    if agen_closed is not _MISSING:
        raw[("inspect", "AGEN_CLOSED")] = agen_closed
    for name in _ERRNO:
        value = _host_attr(errno, name)
        if value is not _MISSING:
            raw[("errno", name)] = int(value)
    for module, names in _INT_SURFACE.items():
        mod = importlib.import_module(module)
        for name in names:
            value = _host_attr(mod, name)
            if value is not _MISSING:
                raw[(module, name)] = int(value)
    for module, names in _STR_SURFACE.items():
        mod = importlib.import_module(module)
        for name in names:
            value = _host_attr(mod, name)
            if value is not _MISSING:
                raw[(module, name)] = value
    for module, names in _BOOL_SURFACE.items():
        mod = importlib.import_module(module)
        for name in names:
            value = _host_attr(mod, name)
            if value is not _MISSING:
                raw[(module, name)] = bool(value)
    found: dict[tuple[str, str], tuple[Any, dict[str, Any]]] = {}
    for key, value in raw.items():
        shaped = _ir_const(value)
        if shaped is not None:
            found[key] = shaped
    sizeof: dict[str, int] = {}
    for name in CTYPES:
        sizeof[name] = ctypes.sizeof(getattr(ctypes, name))
    return found, sizeof


CONSTANTS, SIZEOF = _load_constants()


def module_constant(module: str, member: str) -> tuple[Any, dict[str, Any]] | None:
    return CONSTANTS.get((module, member))


def class_constant(module: str, member: str) -> tuple[T, str] | None:
    """The type and runtime symbol of a class constant, like `date.max`."""
    return CLASS_CONSTANTS.get((module, member))


def fixed_spec(module: str, member: str) -> dict[str, Any] | None:
    """The dict row of a rest call, `sig_for`-cached on first use.

    `REST_FIXED` stores the `Sig` records of the typing-hygiene slice; the
    lowerer reads the dict shape (`_fixed_call`), so this accessor is the
    record-to-dict boundary.
    """

    row = REST_FIXED.get(module, {}).get(member)
    return sig_for(row) if row is not None else None


def type_alias(module: str, member: str) -> str | None:
    return TYPE_ALIAS.get((module, member))


def slot_size(typ: dict[str, Any]) -> int:
    kind: str = typ["kind"]
    if kind in {"int", "float", "list", "dict", "class"}:
        return 8
    if kind in {"str", "bytes"}:
        return 16
    raise ValueError(f"no slot size for {kind}")


def member_callable(module: str, member: str) -> bool:
    if fixed_spec(module, member) is not None:
        return True
    if type_alias(module, member) is not None:
        return True
    if (module, member) in FOR_ONLY or (module, member) in WITH_ONLY:
        return True
    if (module, member) in CALL_FENCES:
        return True
    if module == "operator" and member in OPERATORS:
        return True
    if module == "ctypes" and member in {"sizeof", *CTYPES}:
        return True
    if module == "datetime" and member in {"date", "datetime", "time", "timedelta"}:
        return True
    if module == "time" and member in {"sleep", "gmtime", "localtime"}:
        return True
    if module == "asyncio" and member in {
        "run",
        "sleep",
        "gather",
        "create_task",
        "wait_for",
        "to_thread",
        "Lock",
        "Event",
        "Semaphore",
        "Queue",
    }:
        return True
    if module == "collections" and member in {"deque", "OrderedDict", "Counter", "defaultdict", "namedtuple"}:
        return True
    if module == "functools" and member == "reduce":
        return True
    if module == "posixpath" and member == "join":
        return True
    if module == "pathlib" and member == "Path":
        return True
    if module == "io" and member in {"StringIO", "BytesIO"}:
        return True
    if module == "bisect" and member in {
        "bisect_left",
        "bisect_right",
        "insort",
        "insort_left",
        "insort_right",
    }:
        return True
    if module == "hmac" and member in {"new", "digest", "compare_digest"}:
        return True
    if module == "unicodedata" and member == "name":
        return True
    if module == "importlib.resources" and member == "files":
        return True
    if module == "tempfile" and member in {"NamedTemporaryFile", "mkstemp"}:
        return True
    if module == "atexit" and member == "register":
        return True
    if module == "enum" and member in {"Enum", "IntEnum", "auto"}:
        return True
    if module == "dataclasses" and member == "dataclass":
        return True
    if module == "random" and member in {
        "seed",
        "random",
        "randint",
        "randrange",
        "uniform",
        "getrandbits",
        "choice",
        "shuffle",
        "choices",
        "sample",
        "randbytes",
        "gauss",
        "normalvariate",
        "lognormvariate",
        "expovariate",
        "triangular",
        "gammavariate",
        "betavariate",
    }:
        return True
    if module == "csv" and member in {"reader", "writer"}:
        return True
    if module == "textwrap" and member in {"fill", "wrap", "shorten"}:
        return True
    if module == "uuid" and member in {"uuid1", "uuid4", "uuid5", "UUID"}:
        return True
    if module == "binascii" and member in {"unhexlify", "hexlify"}:
        return True
    if module == "shlex" and member in {"split", "quote", "join"}:
        return True
    if module == "secrets" and member in {"token_hex", "token_bytes", "randbelow"}:
        return True
    if module == "tomllib" and member == "loads":
        return True
    if module == "dotenv" and member in {"dotenv_values", "load_dotenv"}:
        return True
    if module == "yaml" and member == "safe_load":
        return True
    if module == "jwt" and member in {"encode", "decode"}:
        return True
    if module == "orjson" and member in {"dumps", "loads"}:
        return True
    if module == "copy" and member in {"copy", "deepcopy"}:
        return True
    if module == "threading" and member in {
        "Lock",
        "RLock",
        "get_ident",
        "Thread",
        "Event",
        "Condition",
        "Semaphore",
    }:
        return True
    if module == "itertools" and member in {
        "chain",
        "chain.from_iterable",
        "starmap",
        "filterfalse",
        "pairwise",
        "batched",
        "dropwhile",
        "compress",
        "tee",
        "permutations",
    }:
        return True
    if module == "argparse" and member == "ArgumentParser":
        return True
    if module == "queue" and member in {"Queue", "LifoQueue", "SimpleQueue", "Empty", "Full"}:
        return True
    if module == "urllib.request" and member in {"urlopen", "Request"}:
        return True
    return False
