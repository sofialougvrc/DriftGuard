#include "driftguard/perf_collector.hpp"

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

#if defined(__linux__)
#include <sched.h>
#endif

namespace {

struct Options {
  std::string commit{"HEAD"};
  std::string function_name{"benchmark"};
  int iterations{30};
  std::optional<int> pin_cpu;
  bool realtime{false};
  int realtime_priority{80};
  bool prefault_stack{false};
  bool require_runner_controls{false};
  int command_index{-1};
};

void usage() {
  std::cerr << "usage: driftguard-perf --commit <sha> --function <name> "
            << "[--iterations <n>] [--pin-cpu <n>] [--realtime] "
            << "[--realtime-priority <n>] [--prefault-stack] "
            << "[--require-runner-controls] -- <command> [args...]\n";
}

Options parse_options(int argc, char** argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--") {
      options.command_index = i + 1;
      return options;
    }
    if (arg == "--commit" && i + 1 < argc) {
      options.commit = argv[++i];
    } else if (arg == "--function" && i + 1 < argc) {
      options.function_name = argv[++i];
    } else if (arg == "--iterations" && i + 1 < argc) {
      options.iterations = std::stoi(argv[++i]);
    } else if (arg == "--pin-cpu" && i + 1 < argc) {
      options.pin_cpu = std::stoi(argv[++i]);
    } else if (arg == "--realtime") {
      options.realtime = true;
    } else if (arg == "--realtime-priority" && i + 1 < argc) {
      options.realtime_priority = std::stoi(argv[++i]);
    } else if (arg == "--prefault-stack") {
      options.prefault_stack = true;
    } else if (arg == "--require-runner-controls") {
      options.require_runner_controls = true;
    } else if (arg == "--help") {
      usage();
      std::exit(0);
    } else {
      throw std::runtime_error("unknown option: " + arg);
    }
  }
  return options;
}

std::string shell_join(const std::vector<std::string>& parts) {
  std::string command;
  for (std::size_t i = 0; i < parts.size(); ++i) {
    if (i > 0) {
      command += " ";
    }
    command += "'";
    for (char ch : parts[i]) {
      if (ch == '\'') {
        command += "'\\''";
      } else {
        command += ch;
      }
    }
    command += "'";
  }
  return command;
}

bool prefault_stack() {
  volatile char stack_prefault[65536];
  std::memset((void*)stack_prefault, 0, sizeof(stack_prefault));
  return stack_prefault[0] == 0;
}

bool apply_runner_controls(const Options& options) {
  bool ok = true;
  if (options.prefault_stack) {
    ok = prefault_stack() && ok;
  }

#if defined(__linux__)
  if (options.pin_cpu.has_value()) {
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(*options.pin_cpu, &cpuset);
    if (sched_setaffinity(0, sizeof(cpuset), &cpuset) != 0) {
      std::cerr << "driftguard-perf: warning: failed to pin CPU " << *options.pin_cpu << '\n';
      ok = false;
    }
  }

  if (options.realtime) {
    sched_param param{};
    param.sched_priority = options.realtime_priority;
    if (sched_setscheduler(0, SCHED_FIFO, &param) != 0) {
      std::cerr << "driftguard-perf: warning: failed to set SCHED_FIFO priority "
                << options.realtime_priority << '\n';
      ok = false;
    }
  }
#else
  if (options.pin_cpu.has_value() || options.realtime) {
    std::cerr << "driftguard-perf: warning: CPU affinity/realtime controls require Linux\n";
    ok = false;
  }
#endif

  return ok;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const Options options = parse_options(argc, argv);
    if (options.command_index < 0 || options.command_index >= argc) {
      usage();
      return 2;
    }
    if (options.iterations < 1) {
      std::cerr << "--iterations must be positive\n";
      return 2;
    }
    if (options.realtime_priority < 1 || options.realtime_priority > 99) {
      std::cerr << "--realtime-priority must be between 1 and 99\n";
      return 2;
    }
    if (!apply_runner_controls(options) && options.require_runner_controls) {
      std::cerr << "driftguard-perf: required runner controls could not be applied\n";
      return 2;
    }

    const std::vector<std::string> command =
        driftguard::split_command(argc, argv, options.command_index);
    const std::string joined = shell_join(command);
    driftguard::PerfCollector collector;
    int final_exit_code = 0;

    for (int iteration = 0; iteration < options.iterations; ++iteration) {
      auto observation = collector.measure(options.commit, options.function_name, iteration, [&]() {
        return std::system(joined.c_str());
      });
      final_exit_code = observation.exit_code;
      std::cout << driftguard::to_jsonl(observation) << '\n';
      if (final_exit_code != 0) {
        break;
      }
    }

    return final_exit_code == 0 ? 0 : 1;
  } catch (const std::exception& exc) {
    std::cerr << "driftguard-perf: " << exc.what() << '\n';
    return 2;
  }
}
