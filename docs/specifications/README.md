# Weft System Specifications

This directory contains the comprehensive specification documents for the Weft task execution system.

## Document Overview

### **[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)**
System overview, design principles, and high-level architecture. Start here for understanding Weft's core mission and approach.

**Key Topics:**
- Executive summary and core mission
- Why SimpleBroker fits this use case
- Key design principles (Queue-First, Recursive Tasks, etc.)
- Layered architecture diagram
- Performance targets and security model

### **[01-Core_Components.md](01-Core_Components.md)**
Detailed architecture of the fundamental system components.

**Key Topics:**
- TaskSpec implementation with partial immutability
- Task execution engine with process title management
- Executor types (Function vs Command)
- ResourceMonitor with psutil integration
- Queue system integration patterns

### **[02-TaskSpec.md](02-TaskSpec.md)**
Complete specification of the TaskSpec format - the core configuration structure for all tasks.

**Key Topics:**
- JSON schema with examples
- Field descriptions and validation rules
- Limits structure (memory, CPU, file descriptors, connections)
- weft_context for directory scoping
- Process title and output handling configuration

### **[03-Worker_Architecture.md](03-Worker_Architecture.md)**
The recursive "Workers as Tasks" model where workers follow the same patterns as regular tasks, providing system uniformity.

**Key Topics:**
- Workers are Tasks with long-running targets
- TID correlation (Message ID becomes Task ID)
- Bootstrap sequence and worker hierarchy
- Process creation strategy (multiprocessing.spawn)
- Emergency task management via OS tools

### **[04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)**
How Weft leverages SimpleBroker's features without reimplementation.

**Key Topics:**
- Queue operations and message management
- Safe patterns (reservation, peek-and-delete)
- Database management and performance features
- weft_context and directory scoping
- Context isolation and management

### **[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)**
Communication patterns and queue-based state management.

**Key Topics:**
- 8 core message flow patterns
- State machine with forward-only transitions
- Large output handling (>10MB spillover to disk)
- Unified reservation pattern (WIP + dead letter queue)
- Queue lifecycle management

### **[06-Resource_Management.md](06-Resource_Management.md)**
Resource monitoring, limit enforcement, and comprehensive error handling.

**Key Topics:**
- Memory, CPU, file descriptor, and network monitoring
- Resource limit enforcement strategies
- Error handling with reservation pattern
- Security considerations and command validation
- Recovery strategies for failed tasks

### **[07-System_Invariants.md](07-System_Invariants.md)**
Fundamental guarantees and constraints that the system maintains.

**Key Topics:**
- 54 system invariants across 9 categories
- Invariant checking and enforcement
- Runtime validation and error classes
- Testing invariants and monitoring

### **[08-Testing_Strategy.md](08-Testing_Strategy.md)**
Comprehensive testing approach covering all system aspects.

**Key Topics:**
- Unit testing strategy for all components
- Integration testing with SimpleBroker
- System testing and cross-platform validation
- Performance testing and benchmarks
- Property-based testing with Hypothesis

### **[09-Implementation_Plan.md](09-Implementation_Plan.md)**
Development roadmap with 4-week implementation schedule.

**Key Topics:**
- Phase-by-phase implementation plan
- Dependencies and platform support
- Quality gates and risk mitigation
- Future enhancements and success metrics

### **[10-CLI_Interface.md](10-CLI_Interface.md)**
Complete command-line interface specification following SimpleBroker patterns.

**Key Topics:**
- Zero-configuration CLI design
- Task execution, monitoring, and control commands
- Queue operations and worker management
- Process management with OS integration
- Pipeline operations and template management

## Quick Navigation by Topic

### **Getting Started**
1. Read [00-Overview_and_Architecture.md](00-Overview_and_Architecture.md) for system understanding
2. Review [01-Core_Components.md](01-Core_Components.md) for component architecture
3. Study [02-TaskSpec.md](02-TaskSpec.md) for configuration format
4. Check [09-Implementation_Plan.md](09-Implementation_Plan.md) for development roadmap

### **Core Architecture**
- **Components**: [01-Core_Components.md](01-Core_Components.md)
- **Configuration**: [02-TaskSpec.md](02-TaskSpec.md)
- **Workers**: [03-Worker_Architecture.md](03-Worker_Architecture.md)
- **Queues**: [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)
- **Communication**: [05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)

### **Operations & Reliability**
- **Resource Management**: [06-Resource_Management.md](06-Resource_Management.md)
- **System Guarantees**: [07-System_Invariants.md](07-System_Invariants.md)
- **Testing**: [08-Testing_Strategy.md](08-Testing_Strategy.md)

### **Development**
- **Implementation Plan**: [09-Implementation_Plan.md](09-Implementation_Plan.md)
- **CLI Interface**: [10-CLI_Interface.md](10-CLI_Interface.md)
- **Testing Strategy**: [08-Testing_Strategy.md](08-Testing_Strategy.md)
- **System Invariants**: [07-System_Invariants.md](07-System_Invariants.md)

## Key Design Wins

### **1. Recursive Architecture**
Workers are Tasks that run long-lived targets, using the same primitive throughout the system. All entities follow identical lifecycle, observability, and control patterns.

### **2. TID Correlation** 
Message timestamps from SimpleBroker become Task IDs, providing end-to-end correlation. A single identifier follows each task from request to completion with chronological ordering.

### **3. Partial Immutability**
TaskSpec configuration (spec/io sections) becomes immutable after creation, while state and metadata remain mutable. This prevents accidental configuration changes while allowing runtime state updates.

### **4. Unified Reservation**
The `.reserved` queue serves dual purposes: work-in-progress tracking and dead-letter storage. This reduces queue proliferation while enabling failure recovery.

### **5. Queue-Based State**
State management uses `weft.tasks.log` queue rather than a separate database. This event sourcing approach eliminates state synchronization complexity.

### **6. Observable Failures**
Failed messages remain in reserved queues with error state tracking. This provides complete failure visibility for debugging and recovery operations.

### **7. Process Title Integration**
Process titles use shell-friendly format (`weft-{tid_short}:{name}:{status}`) that works with standard Unix tools (ps, grep, kill) without requiring shell escaping.

## Architecture Principles

### **Queue-First Design**
All communication flows through persistent message queues, providing natural async coordination and durability.

### **Ephemeral by Design**
Self-cleaning queues match transient task nature - empty queues automatically disappear, reducing operational overhead.

### **Directory Scoping**
Each project gets isolated weft context through SimpleBroker database location, enabling safe coexistence of multiple environments.

### **Observable by Default**
Every task visible in OS process lists, complete audit trail in logs, and recoverable state in queues.

## Performance Targets

- **Task Creation**: 100 tasks/second
- **Queue Throughput**: 1000 messages/second  
- **Concurrent Tasks**: 200 maximum (design target)
- **Task Startup**: <100ms
- **Memory Overhead**: <10MB per task
- **CPU Monitoring**: <2% overhead

## System Guarantees

- **Exactly-once delivery** via SimpleBroker's claim/move semantics
- **Forward-only state transitions** with full audit trail
- **Resource limit enforcement** with configurable policies
- **Zero message loss** under failures through reservation pattern
- **Complete observability** through logs and process titles
- **Cross-platform consistency** via multiprocessing.spawn

---

**Note**: This specification represents the complete design for Weft v1.0. Implementation follows the roadmap in [09-Implementation_Plan.md](09-Implementation_Plan.md) with a 4-week delivery target focusing on core functionality first.