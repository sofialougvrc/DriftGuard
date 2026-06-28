#include "driftguard/perf_collector.hpp"

#include <array>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>

#if defined(DRIFTGUARD_LINUX_PERF)
#include <linux/perf_event.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif

namespace driftguard {
namespace {

#if defined(DRIFTGUARD_LINUX_PERF)
int perf_event_open(perf_event_attr* attr, pid_t pid, int cpu, int group_fd, unsigned long flags) {
  return static_cast<int>(syscall(__NR_perf_event_open, attr, pid, cpu, group_fd, flags));
}

class PerfEvent {
 public:
  PerfEvent(std::uint32_t type, std::uint64_t config) {
    perf_event_attr attr{};
    attr.type = type;
    attr.size = sizeof(perf_event_attr);
    attr.config = config;
    attr.disabled = 1;
    attr.exclude_kernel = 1;
    attr.exclude_hv = 1;

    fd_ = perf_event_open(&attr, 0, -1, -1, 0);
  }

  PerfEvent(const PerfEvent&) = delete;
  PerfEvent& operator=(const PerfEvent&) = delete;

  PerfEvent(PerfEvent&& other) noexcept : fd_(std::exchange(other.fd_, -1)) {}

  ~PerfEvent() {
    if (fd_ >= 0) {
      close(fd_);
    }
  }

  bool available() const { return fd_ >= 0; }

  void reset_and_enable() const {
    if (fd_ < 0) {
      return;
    }
    ioctl(fd_, PERF_EVENT_IOC_RESET, 0);
    ioctl(fd_, PERF_EVENT_IOC_ENABLE, 0);
  }

  void disable() const {
    if (fd_ >= 0) {
      ioctl(fd_, PERF_EVENT_IOC_DISABLE, 0);
    }
  }

  std::optional<std::uint64_t> read_value() const {
    if (fd_ < 0) {
      return std::nullopt;
    }
    std::uint64_t value = 0;
    if (::read(fd_, &value, sizeof(value)) != sizeof(value)) {
      return std::nullopt;
    }
    return value;
  }

 private:
  int fd_{-1};
};
#endif

std::string shell_join(const std::vector<std::string>& parts) {
  std::ostringstream command;
  for (std::size_t i = 0; i < parts.size(); ++i) {
    if (i > 0) {
      command << ' ';
    }
    command << '\'';
    for (char ch : parts[i]) {
      if (ch == '\'') {
        command << "'\\''";
      } else {
        command << ch;
      }
    }
    command << '\'';
  }
  return command.str();
}

void append_json_string(std::ostringstream& out, const std::string& value) {
  out << '"';
  for (char ch : value) {
    switch (ch) {
      case '\\':
        out << "\\\\";
        break;
      case '"':
        out << "\\\"";
        break;
      case '\n':
        out << "\\n";
        break;
      case '\r':
        out << "\\r";
        break;
      case '\t':
        out << "\\t";
        break;
      default:
        out << ch;
    }
  }
  out << '"';
}

}  // namespace

BenchmarkObservation PerfCollector::measure(const std::string& commit,
                                            const std::string& function_name,
                                            int iteration,
                                            const std::function<int()>& operation) {
#if defined(DRIFTGUARD_LINUX_PERF)
  PerfEvent cycles(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES);
  PerfEvent instructions(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS);
  PerfEvent cache_misses(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CACHE_MISSES);

  cycles.reset_and_enable();
  instructions.reset_and_enable();
  cache_misses.reset_and_enable();
#endif

  const auto start = std::chrono::steady_clock::now();
  const int exit_code = operation();
  const auto end = std::chrono::steady_clock::now();

#if defined(DRIFTGUARD_LINUX_PERF)
  cycles.disable();
  instructions.disable();
  cache_misses.disable();
#endif

  BenchmarkObservation observation;
  observation.commit = commit;
  observation.function = function_name;
  observation.iteration = iteration;
  observation.value = static_cast<std::uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count());
  observation.exit_code = exit_code;

#if defined(DRIFTGUARD_LINUX_PERF)
  observation.counters.cycles = cycles.read_value();
  observation.counters.instructions = instructions.read_value();
  observation.counters.cache_misses = cache_misses.read_value();
#endif

  return observation;
}

std::string to_jsonl(const BenchmarkObservation& observation) {
  std::ostringstream out;
  out << "{\"commit\":";
  append_json_string(out, observation.commit);
  out << ",\"function\":";
  append_json_string(out, observation.function);
  out << ",\"metric\":";
  append_json_string(out, observation.metric);
  out << ",\"value\":" << observation.value;
  out << ",\"unit\":";
  append_json_string(out, observation.unit);
  out << ",\"iteration\":" << observation.iteration;
  out << ",\"exit_code\":" << observation.exit_code;
  out << ",\"counters\":{";
  bool wrote_counter = false;
  const auto write_counter = [&](const char* name, std::optional<std::uint64_t> value) {
    if (!value.has_value()) {
      return;
    }
    if (wrote_counter) {
      out << ',';
    }
    append_json_string(out, name);
    out << ':' << *value;
    wrote_counter = true;
  };
  write_counter("cycles", observation.counters.cycles);
  write_counter("instructions", observation.counters.instructions);
  write_counter("cache_misses", observation.counters.cache_misses);
  out << "}}";
  return out.str();
}

JsonlSink::JsonlSink(std::ostream& output) : output_(output) {}

void JsonlSink::emit(const BenchmarkObservation& observation) {
  output_ << to_jsonl(observation) << '\n';
}

ScopedTimer::ScopedTimer(JsonlSink& sink, std::string commit, std::string function_name, int iteration)
    : sink_(sink),
      commit_(std::move(commit)),
      function_name_(std::move(function_name)),
      iteration_(iteration),
      start_(std::chrono::steady_clock::now()) {}

ScopedTimer::~ScopedTimer() noexcept {
  try {
    const auto end = std::chrono::steady_clock::now();
    BenchmarkObservation observation;
    observation.commit = commit_;
    observation.function = function_name_;
    observation.iteration = iteration_;
    observation.value = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(end - start_).count());
    sink_.emit(observation);
  } catch (...) {
  }
}

std::vector<std::string> split_command(int argc, char** argv, int start_index) {
  std::vector<std::string> parts;
  for (int i = start_index; i < argc; ++i) {
    parts.emplace_back(argv[i]);
  }
  return parts;
}

int run_shell_command(const std::vector<std::string>& parts) {
  return std::system(shell_join(parts).c_str());
}

}  // namespace driftguard
