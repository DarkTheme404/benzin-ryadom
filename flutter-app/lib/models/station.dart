class Station {
  final int id;
  final String name;
  final String? address;
  final String? city;
  final double? lat;
  final double? lon;
  final String? network;
  final String? operator;
  final Map<String, FuelPrice> prices;
  final Map<String, String> availability;
  final String? lastUpdate;
  final double? distance;
  final double? rating;
  final int? reportCount;
  final List<String> limits;
  final bool canisterBan;

  Station({
    required this.id,
    required this.name,
    this.address,
    this.city,
    this.lat,
    this.lon,
    this.network,
    this.operator,
    this.prices = const {},
    this.availability = const {},
    this.lastUpdate,
    this.distance,
    this.rating,
    this.reportCount,
    this.limits = const [],
    this.canisterBan = false,
  });

  factory Station.fromJson(Map<String, dynamic> json) {
    final prices = <String, FuelPrice>{};
    if (json['prices'] != null) {
      (json['prices'] as Map<String, dynamic>).forEach((key, value) {
        if (value is Map<String, dynamic>) {
          prices[key] = FuelPrice.fromJson(value);
        }
      });
    }

    final availability = <String, String>{};
    if (json['availability'] != null) {
      (json['availability'] as Map<String, dynamic>).forEach((key, value) {
        availability[key] = value.toString();
      });
    }

    final limits = <String>[];
    if (json['limits'] != null) {
      limits.addAll((json['limits'] as List).map((e) => e.toString()));
    }

    return Station(
      id: json['id'] ?? 0,
      name: json['name'] ?? '',
      address: json['address'],
      city: json['city'],
      lat: json['lat']?.toDouble(),
      lon: json['lon']?.toDouble(),
      network: json['network'],
      operator: json['operator'],
      prices: prices,
      availability: availability,
      lastUpdate: json['last_update'] ?? json['updated_at'],
      distance: json['distance']?.toDouble(),
      rating: json['rating']?.toDouble(),
      reportCount: json['report_count'],
      limits: limits,
      canisterBan: json['canister_ban'] ?? false,
    );
  }

  String? get mainPrice {
    for (final fuel in ['95', '92', '98', 'diesel', 'lpg']) {
      if (prices.containsKey(fuel) && prices[fuel]!.price != null) {
        return prices[fuel]!.priceText;
      }
    }
    return null;
  }

  String get fuelStatus {
    final statuses = availability.values.toList();
    if (statuses.isEmpty) return 'no_data';
    if (statuses.every((s) => s == 'in_stock')) return 'in_stock';
    if (statuses.every((s) => s == 'out_of_stock')) return 'out_of_stock';
    return 'partial';
  }
}

class FuelPrice {
  final double? price;
  final String? source;
  final String? date;
  final int? priority;

  FuelPrice({this.price, this.source, this.date, this.priority});

  factory FuelPrice.fromJson(Map<String, dynamic> json) {
    return FuelPrice(
      price: json['price']?.toDouble(),
      source: json['source'],
      date: json['date'] ?? json['updated_at'],
      priority: json['priority'],
    );
  }

  String get priceText {
    if (price == null) return '—';
    return '${price!.toStringAsFixed(1)} ₽';
  }
}
