class UserProfile {
  final int id;
  final String? name;
  final String? username;
  final int? telegramId;
  final int? vkId;
  final String? premium;
  final DateTime? premiumExpires;
  final bool isFounder;
  final int reportCount;
  final double savings;
  final String? vkProfileLink;
  final String? tgProfileLink;

  UserProfile({
    required this.id,
    this.name,
    this.username,
    this.telegramId,
    this.vkId,
    this.premium,
    this.premiumExpires,
    this.isFounder = false,
    this.reportCount = 0,
    this.savings = 0,
    this.vkProfileLink,
    this.tgProfileLink,
  });

  factory UserProfile.fromJson(Map<String, dynamic> json) {
    return UserProfile(
      id: json['id'] ?? 0,
      name: json['name'] ?? json['first_name'],
      username: json['username'],
      telegramId: json['telegram_id'],
      vkId: json['vk_id'],
      premium: json['tier'] ?? json['premium_tier'],
      premiumExpires: json['premium_expires'] != null
          ? DateTime.tryParse(json['premium_expires'].toString())
          : null,
      isFounder: json['is_founder'] ?? false,
      reportCount: json['report_count'] ?? 0,
      savings: (json['savings'] ?? 0).toDouble(),
      vkProfileLink: json['vk_profile_link'],
      tgProfileLink: json['tg_profile_link'],
    );
  }

  bool get hasPremium => premium != null && premium != 'none';

  String get displayName {
    if (name != null && name!.isNotEmpty) return name!;
    if (username != null) return '@$username';
    return 'Пользователь';
  }
}
