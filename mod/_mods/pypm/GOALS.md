# PyPM - Python Process Manager

## Core Philosophy

**Build Simply. Build Brilliantly.**

This project embodies the Leonardo da Vinci principle: elegant simplicity that solves complex problems. PyPM is a native Python process manager that eliminates Docker overhead while providing PM2-like functionality.

## Primary Goals

### 1. **Zero-Dependency Process Management**
- Pure Python implementation using only `psutil` and standard library
- No Docker, no containers, no unnecessary complexity
- Direct process control with maximum efficiency

### 2. **Multi-Environment Python Support**
- Seamless virtualenv integration
- Conda environment compatibility
- System Python fallback
- Automatic environment resolution

### 3. **Production-Ready Reliability**
- Process persistence across system restarts
- Automatic restart capabilities
- Comprehensive logging (stdout/stderr separation)
- Resource monitoring (CPU, memory, uptime)

### 4. **Developer Experience**
- Intuitive CLI interface
- Programmatic API for integration
- Real-time log streaming
- Detailed process inspection

## Technical Objectives

### Completed ✓
- [x] Core process lifecycle (start, stop, restart, delete)
- [x] Process registry with JSON persistence
- [x] Multi-interpreter support (Python, Node, Bash)
- [x] Environment variable management
- [x] Log file management and tailing
- [x] Process monitoring (CPU, memory, status)
- [x] Bulk operations (kill-all, resurrect)
- [x] Python environment resolution

### In Progress
- [ ] Auto-restart on failure
- [ ] Process clustering
- [ ] Load balancing
- [ ] Web dashboard

### Future Enhancements
- [ ] Configuration file support (JSON/YAML)
- [ ] Process dependencies and startup order
- [ ] Resource limits (CPU/memory caps)
- [ ] Notification system (webhooks, email)
- [ ] Metrics export (Prometheus, StatsD)

## Design Principles

1. **Simplicity Over Complexity**: Every feature must justify its existence
2. **Native Over Abstracted**: Use OS primitives directly when possible
3. **Explicit Over Implicit**: Clear, readable code over clever tricks
4. **Reliable Over Feature-Rich**: Core functionality must be bulletproof

## Success Metrics

- **Performance**: Sub-second process start/stop times
- **Reliability**: 99.9% uptime for managed processes
- **Usability**: Single command to start managing processes
- **Compatibility**: Works on Linux, macOS, Windows

## The Vision

PyPM aims to be the **definitive Python process manager** - the tool you reach for when you need to run Python services in production without the overhead of containerization. It's PM2 for the Python ecosystem, built by developers who value simplicity and reliability above all else.

---

*"Simplicity is the ultimate sophistication." - Leonardo da Vinci*
