class FuelRoute {
  final int id;
  final String name;
  final String? code;
  final String? type;
  final int? stationCount;

  FuelRoute({
    required this.id,
    required this.name,
    this.code,
    this.type,
    this.stationCount,
  });

  factory FuelRoute.fromJson(Map<String, dynamic> json) {
    return FuelRoute(
      id: json['id'] ?? 0,
      name: json['name'] ?? '',
      code: json['code'],
      type: json['type'],
      stationCount: json['station_count'],
    );
  }
}
