#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

namespace {

struct Options {
  std::string variant{"baseline"};
  std::uint64_t seed{42};
  int orders{80000};
  int repeat{18};
  bool quiet{false};
};

struct Order {
  std::uint64_t id;
  std::uint64_t customer_id;
  std::uint64_t sku;
  std::uint64_t cents;
};

std::uint64_t xorshift64(std::uint64_t& state) {
  state ^= state << 13;
  state ^= state >> 7;
  state ^= state << 17;
  return state;
}

int parse_int(const char* value, const char* name) {
  char* end = nullptr;
  const long parsed = std::strtol(value, &end, 10);
  if (end == value || *end != '\0' || parsed <= 0) {
    throw std::runtime_error(std::string("invalid ") + name + ": " + value);
  }
  return static_cast<int>(parsed);
}

Options parse_options(int argc, char** argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--variant" && i + 1 < argc) {
      options.variant = argv[++i];
    } else if (arg == "--orders" && i + 1 < argc) {
      options.orders = parse_int(argv[++i], "--orders");
    } else if (arg == "--repeat" && i + 1 < argc) {
      options.repeat = parse_int(argv[++i], "--repeat");
    } else if (arg == "--seed" && i + 1 < argc) {
      options.seed = static_cast<std::uint64_t>(parse_int(argv[++i], "--seed"));
    } else if (arg == "--quiet") {
      options.quiet = true;
    } else if (arg == "--help") {
      std::cout << "usage: order-service-bench [--variant baseline|regressed] "
                   "[--orders n] [--repeat n] [--seed n] [--quiet]\n";
      std::exit(0);
    } else {
      throw std::runtime_error("unknown option: " + arg);
    }
  }
  if (options.variant != "baseline" && options.variant != "regressed") {
    throw std::runtime_error("--variant must be baseline or regressed");
  }
  return options;
}

std::vector<Order> make_orders(int count, std::uint64_t seed) {
  std::vector<Order> orders;
  orders.reserve(static_cast<std::size_t>(count));
  std::uint64_t state = seed;
  for (int i = 0; i < count; ++i) {
    const std::uint64_t id = xorshift64(state);
    orders.push_back(
        Order{
            id,
            xorshift64(state) % 25000,
            xorshift64(state) % 4000,
            500 + (xorshift64(state) % 60000),
        });
  }
  return orders;
}

std::uint64_t process_orders(const std::vector<Order>& orders, const Options& options) {
  std::uint64_t accumulator = 1469598103934665603ull;
  const int extra_repeat = options.variant == "regressed" ? std::max(1, options.repeat / 3) : 0;

  for (int pass = 0; pass < options.repeat; ++pass) {
    for (const Order& order : orders) {
      const std::uint64_t risk = ((order.customer_id * 1315423911ull) ^ order.sku ^ pass) & 1023ull;
      const std::uint64_t tax = (order.cents * (725 + (risk % 31))) / 10000;
      const std::uint64_t discount = ((risk * 17) + (order.id & 255ull)) % 700;
      accumulator ^= order.id + tax - std::min(discount, order.cents / 10);
      accumulator *= 1099511628211ull;
    }
  }

  // The regressed variant models a realistic small algorithmic tax: an extra
  // validation pass over the same order set rather than an artificial sleep.
  for (int pass = 0; pass < extra_repeat; ++pass) {
    for (const Order& order : orders) {
      accumulator ^= (order.customer_id + 31ull * order.sku + pass) * 11400714819323198485ull;
      accumulator = (accumulator << 7) | (accumulator >> 57);
    }
  }

  return accumulator;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const Options options = parse_options(argc, argv);
    const std::vector<Order> orders = make_orders(options.orders, options.seed);
    const std::uint64_t result = process_orders(orders, options);
    if (!options.quiet) {
      std::cerr << "order-service-bench " << options.variant << " checksum=" << result << '\n';
    }
    return result == 0 ? 1 : 0;
  } catch (const std::exception& exc) {
    std::cerr << "order-service-bench: " << exc.what() << '\n';
    return 2;
  }
}
