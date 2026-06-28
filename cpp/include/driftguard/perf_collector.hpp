#pragma once

#include <chrono>
#include <cstdint>
#include <functional>
#include <iosfwd>
#include <optional>
#include <string>
#include <vector>

namespace driftguard {

struct PerfCounters {
  std::optional<std::uint64_t> cycles;
  std::optional<std::uint64_t> instructions;
  std::optional<std::uint64_t> cache_misses;
};

struct BenchmarkObservation {
  std::string commit;
  std::string function;
  std::string metric{"latency_ns"};
  std::uint64_t value{0};
  std::string unit{"ns"};
  int iteration{0};
  PerfCounters counters;
  int exit_code{0};
};

class PerfCollector {
 public:
  BenchmarkObservation measure(const std::string& commit,
                               const std::string& function_name,
                               int iteration,
                               const std::function<int()>& operation);
};

class JsonlSink {
 public:
  explicit JsonlSink(std::ostream& output);
  void emit(const BenchmarkObservation& observation);

 private:
  std::ostream& output_;
};

class ScopedTimer {
 public:
  ScopedTimer(JsonlSink& sink, std::string commit, std::string function_name, int iteration = 0);
  ScopedTimer(const ScopedTimer&) = delete;
  ScopedTimer& operator=(const ScopedTimer&) = delete;
  ~ScopedTimer() noexcept;

 private:
  JsonlSink& sink_;
  std::string commit_;
  std::string function_name_;
  int iteration_;
  std::chrono::steady_clock::time_point start_;
};

std::string to_jsonl(const BenchmarkObservation& observation);
std::vector<std::string> split_command(int argc, char** argv, int start_index);

}  // namespace driftguard

#define DRIFTGUARD_CONCAT_INNER(a, b) a##b
#define DRIFTGUARD_CONCAT(a, b) DRIFTGUARD_CONCAT_INNER(a, b)
#define DRIFTGUARD_SCOPE(sink, commit, function_name) \
  driftguard::ScopedTimer DRIFTGUARD_CONCAT(driftguard_scope_, __LINE__)(sink, commit, function_name)
