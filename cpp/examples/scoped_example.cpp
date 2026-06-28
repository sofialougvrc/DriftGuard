#include "driftguard/perf_collector.hpp"

#include <cmath>
#include <iostream>
#include <string>

double process_order(driftguard::JsonlSink& sink, const std::string& commit, int iteration) {
  driftguard::ScopedTimer timer(sink, commit, "processOrder", iteration);
  double total = 0.0;
  for (int i = 0; i < 25000; ++i) {
    total += std::sqrt(static_cast<double>(i + 1));
  }
  return total;
}

int main(int argc, char** argv) {
  const std::string commit = argc > 1 ? argv[1] : "local";
  driftguard::JsonlSink sink(std::cout);
  double guard = 0.0;
  for (int iteration = 0; iteration < 10; ++iteration) {
    guard += process_order(sink, commit, iteration);
  }
  return guard > 0.0 ? 0 : 1;
}
